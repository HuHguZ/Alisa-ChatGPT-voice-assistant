"""Host ALSA integration checks, using synthetic samples and the null device."""
import array
import os
import re
from pathlib import Path
import select
import socket
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AudioHelpers(unittest.TestCase):
    def test_recovery_refills_alsa_and_reports_the_error_without_losing_blocks(self):
        # Simulate one underrun using real ALSA's null device; no speaker or
        # microphone is opened. Also report a smaller hardware buffer to check
        # that the start threshold fits the device instead of waiting forever.
        source = r'''
#include <dlfcn.h>
int snd_pcm_get_params(void *pcm, unsigned long *buffer, unsigned long *period) {
    *buffer = 960; *period = 240; return 0;
}
long snd_pcm_writei(void *pcm, const void *samples, unsigned long frames) {
    static unsigned int calls;
    if (++calls == 2) return -32;
    long (*next)(void *, const void *, unsigned long) = dlsym(RTLD_NEXT, "snd_pcm_writei");
    return next(pcm, samples, frames);
}
'''
        with tempfile.TemporaryDirectory(prefix='alice-recovery-test-') as temp:
            fixture = Path(temp)
            (fixture/'alsa_fixture.c').write_text(source)
            subprocess.run(['clang', '-shared', '-fPIC', str(fixture/'alsa_fixture.c'),
                            '-o', str(fixture/'alsa_fixture.so'), '-ldl'], check=True)
            env = {**os.environ, 'ALICE_PLAYBACK_WORKER': '1',
                   'ALICE_PLAYBACK_DEVICE': 'null',
                   'LD_PRELOAD': str(ROOT/'build/playback_worker.host.so') + ':' +
                                 str(fixture/'alsa_fixture.so')}
            proc = subprocess.run(['aplay', '--version'], input=bytes(3840 * 6),
                                  env=env, capture_output=True, timeout=3)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
            self.assertEqual(proc.stdout, b'READY\nKUKKKK')
            self.assertIn(b'ALICE_PLAYBACK_RECOVER error=-32', proc.stderr)
            match = re.search(rb'buffer=(\d+) period=(\d+) prefill=(\d+)', proc.stderr)
            self.assertIsNotNone(match)
            buffer, period, prefill = map(int, match.groups())
            self.assertGreater(prefill, 1)
            self.assertLessEqual(prefill, buffer - period)

    def test_playback_acknowledges_complete_blocks_and_exits_on_eof(self):
        env = {**os.environ, 'LD_PRELOAD': str(ROOT / 'build/playback_worker.host.so'),
               'ALICE_PLAYBACK_WORKER': '1', 'ALICE_PLAYBACK_DEVICE': 'null'}
        proc = subprocess.Popen(['aplay', '--version'], env=env, bufsize=0,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        try:
            self.assertTrue(select.select([proc.stdout], [], [], 3)[0])
            self.assertEqual(proc.stdout.read(6), b'READY\n')
            proc.stdin.write(b'\x01\x00' * 960)
            self.assertFalse(select.select([proc.stdout], [], [], .1)[0])
            proc.stdin.write(b'\x02\x00' * 960)
            self.assertEqual(proc.stdout.read(1), b'K')
            for _ in range(5):
                proc.stdin.write(bytes(3840))
                self.assertEqual(proc.stdout.read(1), b'K')
            proc.stdin.close()
            self.assertEqual(proc.wait(timeout=3), 0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            for stream in [proc.stdin, proc.stdout, proc.stderr]:
                stream.close()

    def capture_fixture(self, channels, muted, receiver):
        original = ([1000, 3000, 20000] if channels == 3 else
                    [1000, 90, 3000, 70, 60, 50, 20000, 30])
        with tempfile.TemporaryDirectory(prefix='alice-audio-test-') as temp:
            d = Path(temp)
            (d / 'input.raw').write_bytes(array.array('h', original * 48000).tobytes())
            (d / 'asound.conf').write_text(
                'pcm.null { type null }\n'
                f'pcm.test_capture {{ type file slave.pcm "null" file "/dev/null" '
                f'infile "{d}/input.raw" format "raw" }}\n')
            if muted:
                (d / 'muted').touch()
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            sock.settimeout(.05)
            received = []
            done = threading.Event()
            thread = None
            if receiver != 'missing':
                sock.bind(str(d / 'mic.sock'))
            if receiver == 'reading':
                def consume():
                    while not done.is_set():
                        try:
                            received.append(sock.recv(4096))
                        except TimeoutError:
                            pass
                thread = threading.Thread(target=consume)
                thread.start()
            env = {**os.environ,
                   'LD_PRELOAD': str(ROOT / 'build/capture_tap.host.so'),
                   'ALSA_CONFIG_PATH': str(d / 'asound.conf'),
                   'ALICE_CAPTURE_SOCKET': str(d / 'mic.sock'),
                   'ALICE_NATIVE_MUTE_FILE': str(d / 'muted')}
            try:
                proc = subprocess.run(
                    ['arecord', '-q', '-D', 'test_capture', '-r', '48000', '-c',
                     str(channels), '-f', 'S16_LE', '-t', 'raw', '-s', '48000'],
                    env=env, capture_output=True, timeout=3)
            finally:
                done.set()
                if thread:
                    thread.join(timeout=1)
                sock.close()
            self.assertEqual(proc.returncode, 0, proc.stderr.decode(errors='replace'))
            actual = array.array('h')
            actual.frombytes(proc.stdout)
            expected = original.copy()
            if muted:
                expected[0] = expected[1 if channels == 3 else 2] = 0
            self.assertEqual(actual, array.array('h', expected * 48000))
            if receiver == 'reading':
                tap = array.array('h')
                tap.frombytes(b''.join(received))
                self.assertGreater(len(tap), 0)
                self.assertEqual(set(tap), {2000})

    def test_native_mute_preserves_bridge_microphones_and_speaker_feedback(self):
        for channels in [3, 8]:
            for muted in [False, True]:
                with self.subTest(channels=channels, muted=muted):
                    self.capture_fixture(channels, muted, 'reading')

    def test_missing_or_stalled_receiver_cannot_block_native_capture(self):
        for receiver in ['missing', 'stalled']:
            with self.subTest(receiver=receiver):
                self.capture_fixture(3, False, receiver)


if __name__ == '__main__':
    unittest.main()
