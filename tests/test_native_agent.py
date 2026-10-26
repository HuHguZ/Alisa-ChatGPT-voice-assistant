"""Run the actual station controller against a local fake Quasar IPC peer."""
import ctypes
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from native_control import byte_field, varint, fields


def integer(key, value):
    return varint(key * 8) + varint(value)


def state_frame(state, text='', request='00000000-0000-4000-8000-000000000001', final=False):
    recognition = byte_field(1, text.encode()) + integer(2, int(final)) + integer(4, 1)
    data = integer(1, state) + byte_field(7, request.encode()) + byte_field(15, recognition)
    data = byte_field(252, data) + integer(253, 3)
    return len(data).to_bytes(4, 'little') + data


class NativeAgent(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.server = socket.socket()
        self.server.bind(('127.0.0.1', 0))
        self.server.listen(1)
        self.server.settimeout(2)
        self.addCleanup(self.server.close)
        self.proc = subprocess.Popen(['/usr/bin/true'], bufsize=0,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, 'LD_PRELOAD': str(ROOT / 'build/native_agent.host.so'),
                 'ALICE_NATIVE_AGENT': '1', 'ALICE_NATIVE_PORT': str(self.server.getsockname()[1]),
                 'ALICE_NATIVE_LOCK': str(Path(self.temp.name) / 'agent.lock')})
        self.addCleanup(self.cleanup)
        self.peer, _ = self.server.accept()
        self.peer.settimeout(.2)
        self.addCleanup(self.peer.close)
        self.assertEqual(self.message()['event'], 'ready')

    def cleanup(self):
        if self.proc.poll() is None:
            if not self.proc.stdin.closed:
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

    def control(self, line):
        self.proc.stdin.write((line + '\n').encode())
        return self.message()

    def wait_state(self, expected):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if self.control('PING')['native_state'] == expected:
                return
            time.sleep(.01)
        self.fail(f'Native state did not become {expected}')

    def exact(self, length):
        data = b''
        while len(data) < length:
            chunk = self.peer.recv(length - len(data))
            self.assertTrue(chunk)
            data += chunk
        return data

    def method(self):
        size = int.from_bytes(self.exact(4), 'little')
        envelope = fields(self.exact(size))
        self.assertEqual(envelope[253], 210)
        remote = fields(envelope[252])
        return fields(remote[8])

    def test_ordinary_music_is_untouched_but_exact_open_is_cancelled_once(self):
        self.peer.sendall(state_frame(3, 'включи музыку', final=True))
        with self.assertRaises(TimeoutError):
            self.peer.recv(1)
        frame = state_frame(3, 'открой чат', request='00000000-0000-4000-8000-000000000002', final=True)
        self.peer.sendall(frame)
        method = self.method()
        self.assertEqual(method[1], 6)
        self.assertEqual(method[10], b'00000000-0000-4000-8000-000000000002')
        self.assertEqual(self.message(), {'event': 'command', 'action': 'open'})
        self.peer.sendall(frame)
        with self.assertRaises(TimeoutError):
            self.peer.recv(1)
        self.assertEqual(self.control('PING')['queue_clear_requests'], 1)

    def test_opening_waits_for_busy_and_new_chat_clears_only_its_request(self):
        request = '00000000-0000-4000-8000-000000000009'
        self.peer.sendall(state_frame(1, 'создай новый чат', request=request, final=True))
        with self.assertRaises(TimeoutError):
            self.peer.recv(1)
        self.peer.sendall(state_frame(3, 'создай новый чат', request=request, final=True))
        method = self.method()
        self.assertEqual(method[1], 6)
        self.assertEqual(method[10], request.encode())
        self.assertEqual(self.message(), {'event': 'command', 'action': 'new'})

    def test_explicit_queue_clear_requires_busy_with_a_current_request(self):
        self.proc.stdin.write(b'CLEAR_QUEUE\n')
        with self.assertRaises(TimeoutError):
            self.peer.recv(1)
        self.peer.sendall(state_frame(1))
        self.proc.stdin.write(b'CLEAR_QUEUE\n')
        with self.assertRaises(TimeoutError):
            self.peer.recv(1)
        request = '00000000-0000-4000-8000-000000000008'
        self.peer.sendall(state_frame(3, 'сколько времени', request=request, final=True))
        # PING synchronizes the IPC state before the explicit control command.
        self.wait_state(3)
        self.proc.stdin.write(b'CLEAR_QUEUE\n')
        method = self.method()
        self.assertEqual(method[1], 6)
        self.assertEqual(method[10], request.encode())
        self.peer.sendall(state_frame(0))
        self.wait_state(0)
        self.peer.sendall(state_frame(3, request=''))
        self.wait_state(3)
        self.proc.stdin.write(b'CLEAR_QUEUE\n')
        with self.assertRaises(TimeoutError):
            self.peer.recv(1)
        self.assertEqual(self.control('PING')['queue_clear_requests'], 1)

    def start_native_session(self):
        self.assertEqual(self.control('CHAT')['mode'], 'chat')
        self.peer.sendall(state_frame(1))
        with self.assertRaises(TimeoutError):
            self.peer.recv(1)

    def test_chat_preserves_wake_recognition_until_final_close(self):
        self.start_native_session()
        self.peer.sendall(state_frame(1, 'закрой чат', final=False))
        with self.assertRaises(TimeoutError):
            self.peer.recv(1)
        self.peer.sendall(state_frame(3, 'закрой чат', final=True))
        self.assertEqual(self.method()[1], 3)
        self.assertEqual(self.message(), {'event': 'command', 'action': 'close'})

    def test_unknown_recognition_cannot_start_native_music_or_switch_mode(self):
        self.start_native_session()
        self.peer.sendall(state_frame(3, 'включи музыку', final=True))
        self.assertEqual(self.method()[1], 3)
        self.peer.sendall(state_frame(0))
        with self.assertRaises(TimeoutError):
            self.peer.recv(1)
        report = self.control('PING')
        self.assertEqual(report['mode'], 'chat')
        self.assertEqual(report['commands'], 0)

    def test_periodic_music_pause_does_not_interrupt_native_recognition(self):
        self.start_native_session()
        self.proc.stdin.write(b'PAUSE\n')
        with self.assertRaises(TimeoutError):
            self.peer.recv(1)
        self.peer.sendall(state_frame(3, 'закрой чат', final=True))
        self.assertEqual(self.method()[1], 3)
        self.assertEqual(self.message()['action'], 'close')

    def test_second_wake_after_unrelated_query_can_close_chat(self):
        self.start_native_session()
        self.peer.sendall(state_frame(3, 'сколько времени', final=True))
        self.assertEqual(self.method()[1], 3)
        self.peer.sendall(state_frame(0))
        request = '00000000-0000-4000-8000-000000000002'
        self.peer.sendall(state_frame(1, request=request))
        with self.assertRaises(TimeoutError):
            self.peer.recv(1)
        self.peer.sendall(state_frame(3, 'закрой чат', request=request, final=True))
        self.assertEqual(self.method()[1], 3)
        self.assertEqual(self.message()['action'], 'close')
        self.assertEqual(self.control('PING')['recognition_starts'], 2)

    def test_final_in_listening_waits_for_busy_and_is_dispatched_once(self):
        self.start_native_session()
        self.peer.sendall(state_frame(1, 'закрой чат', final=True))
        with self.assertRaises(TimeoutError):
            self.peer.recv(1)
        self.peer.sendall(state_frame(3, 'закрой чат', final=True))
        self.assertEqual(self.method()[1], 3)
        self.assertEqual(self.message()['action'], 'close')
        self.peer.sendall(state_frame(3, 'закрой чат', final=True))
        with self.assertRaises(TimeoutError):
            self.peer.recv(1)

    def test_native_speech_is_stopped_in_chat_but_not_in_alice_mode(self):
        self.peer.sendall(state_frame(2))
        with self.assertRaises(TimeoutError):
            self.peer.recv(1)
        self.control('CHAT')
        self.peer.sendall(state_frame(2))
        self.assertEqual(self.method()[1], 14)
        self.assertEqual(self.control('PING')['speech_stops'], 1)

    def test_own_reply_blocks_new_wakes_but_not_a_preceding_close(self):
        self.control('CHAT')
        self.proc.stdin.write(b'SPEAKING\n')
        self.peer.sendall(state_frame(1))
        self.assertEqual(self.method()[1], 3)
        self.proc.stdin.write(b'QUIET\n')
        request = '00000000-0000-4000-8000-000000000002'
        self.peer.sendall(state_frame(1, request=request))
        with self.assertRaises(TimeoutError):
            self.peer.recv(1)
        self.proc.stdin.write(b'SPEAKING\n')
        self.peer.sendall(state_frame(3, 'закрой чат', request='00000000-0000-4000-8000-000000000002', final=True))
        self.assertEqual(self.method()[1], 3)
        self.assertEqual(self.message()['action'], 'close')

    def test_backend_eof_cancels_owned_session_and_exits(self):
        self.start_native_session()
        self.proc.stdin.close()
        self.assertEqual(self.method()[1], 3)
        self.assertEqual(self.proc.wait(timeout=2), 0)

    def test_malformed_final_event_is_ignored(self):
        frame = state_frame(3, 'открой чат', final=True)
        bad = frame[4:] + b'\x0a\xff'
        self.peer.sendall(len(bad).to_bytes(4, 'little') + bad)
        with self.assertRaises(TimeoutError):
            self.peer.recv(1)
        self.assertEqual(self.control('PING')['commands'], 0)


class NativeIntents(unittest.TestCase):
    def test_utf8_commands_and_music_are_distinguished(self):
        library = ctypes.CDLL(str(ROOT / 'build/native_agent.host.so'))
        command = library.alice_native_command
        command.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        command.restype = ctypes.c_int
        for phrase, expected in [('Алиса, открой чат!', 1), ('создай новый чат', 2),
                                 ('Алиса, закрой чат, пожалуйста.', 3),
                                 ('включи музыку', 0), ('Алиса, включи чат', 0),
                                 ('скажи алиса открой чат', 0), ('Алиса, не закрывай чат', 0)]:
            data = phrase.encode()
            self.assertEqual(command(data, len(data)), expected, phrase)
