"""Exercise real native agent ownership against a fake FilePlayer IPC peer."""
import io
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from audio_focus import LEASE_SECONDS, GUARD_TIMEOUT_SECONDS, silent_lease
from native_control import byte_field, fields, rpc_frame, varint
from test_native_agent import state_frame


def integer(key, value):
    return varint(key * 8) + varint(value)


def focus_event(request, *, focused=True, completed=None, started=False, object_name=b'AudioFilePlayerCapability'):
    directive = byte_field(1, byte_field(4, request))
    audio = b''
    if completed is not None:
        directive += integer(4, completed)
    elif started:
        directive += byte_field(3, b'')
    else:
        audio = byte_field(3, integer(1, 6) + integer(17, int(focused)))
    return rpc_frame(object_name, 17, byte_field(1, directive) + audio)


class NativeFocus(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.server = socket.socket()
        self.server.bind(('127.0.0.1', 0))
        self.server.listen(1)
        self.server.settimeout(2)
        self.addCleanup(self.server.close)
        template = Path(temp.name)/'template.wav'
        template.write_bytes(silent_lease())
        self.proc = subprocess.Popen(['/usr/bin/true'], bufsize=0,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, 'LD_PRELOAD': str(ROOT/'build/native_agent.host.so'),
                 'ALICE_NATIVE_AGENT': '1', 'ALICE_NATIVE_FOCUS': '1',
                 'ALICE_FOCUS_TEMPLATE': str(template), 'ALICE_FOCUS_DIRECTORY': temp.name,
                 'ALICE_NATIVE_PORT': str(self.server.getsockname()[1]),
                 'ALICE_NATIVE_LOCK': str(Path(temp.name)/'agent.lock')})
        self.addCleanup(self.cleanup)
        self.peer, _ = self.server.accept()
        self.peer.settimeout(2)
        self.addCleanup(self.peer.close)
        self.assertEqual(self.message()['event'], 'ready')

    def cleanup(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        for stream in [self.proc.stdin, self.proc.stdout, self.proc.stderr]:
            stream.close()

    def message(self):
        self.assertTrue(select.select([self.proc.stdout], [], [], 2)[0])
        return json.loads(self.proc.stdout.readline())

    def command(self, text):
        self.proc.stdin.write((text+'\n').encode())
        return self.message()

    def exact(self, size):
        data = b''
        while len(data) < size:
            chunk = self.peer.recv(size-len(data))
            self.assertTrue(chunk)
            data += chunk
        return data

    def method(self):
        size = int.from_bytes(self.exact(4), 'little')
        envelope = fields(self.exact(size))
        self.assertEqual(envelope[253], 210)
        remote = fields(envelope[252])
        self.assertEqual(remote[7], b'AudioFilePlayerCapability')
        return fields(remote[12])

    def begin(self):
        status = self.command('CHAT')
        self.assertEqual(status['focus_ready'], 0)
        method = self.method()
        self.assertEqual((method[1], method[3], method[5]), (0, 1, 1))
        params = fields(method[4])
        self.assertEqual((params[1], params[2]), (1, 1))
        self.assertEqual(len(params[4]), 36)
        return method[2], params[4]

    def confirm(self, request):
        self.peer.sendall(focus_event(request))
        result = self.message()
        self.assertEqual(result['event'], 'focus')
        self.assertEqual(result['focus_ready'], 1)
        return result

    def test_only_matching_focus_confirmation_grants_readiness(self):
        path, request = self.begin()
        self.peer.sendall(focus_event(request, started=True))
        self.peer.sendall(focus_event(b'00000000-0000-4000-8000-000000000000'))
        self.peer.sendall(focus_event(request, object_name=b'UnrelatedCapability'))
        time.sleep(.05)
        self.assertEqual(self.command('PING')['focus_ready'], 0)
        self.confirm(request)
        self.assertEqual(self.command('IDLE')['focus_ready'], 0)
        stop = self.method()
        self.assertEqual((stop[1], stop[2]), (1, path))
        self.peer.sendall(focus_event(request))
        time.sleep(.03)
        self.assertEqual(self.command('PING')['focus_ready'], 0)
        self.command('IDLE')
        self.assertFalse(select.select([self.peer], [], [], .05)[0])

    def test_one_activity_is_kept_without_periodic_play_or_pause_requests(self):
        path, request = self.begin()
        self.confirm(request)
        deadline = time.monotonic() + 6.3
        while time.monotonic() < deadline:
            self.assertFalse(select.select([self.peer], [], [], .15)[0])
            self.assertEqual(self.command('PING')['focus_ready'], 1)
        self.command('IDLE')
        self.assertEqual(self.method()[2], path)

    def guardian_stop(self, path, timeout=2):
        self.server.settimeout(timeout)
        peer, _ = self.server.accept()
        self.addCleanup(peer.close)
        peer.settimeout(2)
        data = b''
        while len(data) < 4:
            data += peer.recv(4-len(data))
        size = int.from_bytes(data, 'little')
        data = b''
        while len(data) < size:
            part = peer.recv(size-len(data))
            self.assertTrue(part)
            data += part
        remote = fields(fields(data)[252])
        self.assertEqual(remote[7], b'AudioFilePlayerCapability')
        method = fields(remote[12])
        self.assertEqual((method[1], method[2]), (1, path))
        self.assertEqual(peer.recv(1), b'')

    def test_independent_guard_releases_after_agent_sigkill(self):
        path, request = self.begin()
        self.confirm(request)
        self.proc.kill()
        self.proc.wait(timeout=2)
        self.guardian_stop(path)
        self.assertFalse(Path(path.decode()).exists())

    def test_independent_guard_releases_when_agent_is_frozen(self):
        path, request = self.begin()
        self.confirm(request)
        os.kill(self.proc.pid, signal.SIGSTOP)
        try:
            self.guardian_stop(path, GUARD_TIMEOUT_SECONDS+2)
        finally:
            os.kill(self.proc.pid, signal.SIGCONT)

    def test_focus_loss_and_completion_restore_idle(self):
        path, request = self.begin()
        self.confirm(request)
        self.peer.sendall(focus_event(request, focused=False))
        result = self.message()
        self.assertEqual((result['event'], result['mode'], result['focus_ready']),
                         ('focus_error', 'idle', 0))
        self.assertEqual(self.method()[2], path)
        path, request = self.begin()
        self.peer.sendall(focus_event(request, completed=1))
        self.assertEqual(self.message()['event'], 'focus_error')
        self.assertEqual(self.method()[2], path)

    def test_acquire_timeout_removes_pending_activity(self):
        path, request = self.begin()
        deadline = time.monotonic() + 7
        result = {}
        while result.get('event') != 'focus_error':
            time.sleep(.15)
            result = self.command('PING')
            self.assertLess(time.monotonic(), deadline)
        self.assertEqual(result['focus_ready'], 0)
        self.assertEqual(self.method()[2], path)

    def test_eof_releases_our_activity(self):
        path, request = self.begin()
        self.confirm(request)
        self.proc.stdin.close()
        self.assertEqual(self.method()[2], path)
        self.assertEqual(self.proc.wait(timeout=2), 0)

    def test_template_wav_is_finite_and_silent(self):
        with wave.open(io.BytesIO(silent_lease())) as audio:
            self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()),
                             (1, 2, 16000))
            self.assertEqual(audio.getnframes(), 16000*LEASE_SECONDS)
            self.assertFalse(any(audio.readframes(audio.getnframes())))

    def test_entry_pause_waits_for_native_input_to_finish_and_is_not_repeated(self):
        self.peer.sendall(state_frame(1))
        time.sleep(.03)
        self.proc.stdin.write(b'PAUSE\nPING\n')
        self.message()
        self.assertFalse(select.select([self.peer], [], [], .05)[0])
        self.peer.sendall(state_frame(0))
        size = int.from_bytes(self.exact(4), 'little')
        remote = fields(fields(self.exact(size))[252])
        self.assertEqual(remote[7], b'PlaybackControlCapability')
        self.assertEqual(fields(remote[13])[1], 1)
        self.peer.sendall(state_frame(0))
        self.assertFalse(select.select([self.peer], [], [], .05)[0])
