"""Real Opus/AES-GCM and native ALSA-null receiver with network impairments."""
import array
import json
import math
import os
from pathlib import Path
import select
import socket
import subprocess
import tempfile
import time
import unittest

from packet_audio import PacketEncoder, sender_socket

ROOT = Path(__file__).resolve().parents[1]


class PacketAudio(unittest.TestCase):
    def setUp(self):
        self.secret = os.urandom(40)
        self.encoder = PacketEncoder(self.secret)
        self.addCleanup(self.encoder.close)

    def receiver(self, extra_env=None):
        env = {**os.environ, 'LD_PRELOAD': str(ROOT/'build/packet_playback.host.so'),
               'ALICE_PACKET_PLAYBACK': '1', 'ALICE_PACKET_BIND': '127.0.0.1',
               'ALICE_CRYPTO_LIBRARY': 'libcrypto.so.3', 'ALICE_PLAYBACK_DEVICE': 'null'}
        env.update(extra_env or {})
        proc = subprocess.Popen(['aplay', '--version'], env=env, bufsize=0,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        def cleanup():
            if proc.poll() is None:
                proc.stdin.close()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()
        self.addCleanup(cleanup)
        self.assertTrue(select.select([proc.stdout], [], [], 3)[0])
        ready = json.loads(proc.stdout.readline())
        self.assertTrue(ready['ready'])
        proc.stdin.write(self.secret)
        sender = sender_socket((ready['ip'], ready['port']))
        self.addCleanup(sender.close)
        return proc, sender

    def report(self, proc):
        self.assertTrue(select.select([proc.stdout], [], [], 3)[0])
        line = proc.stdout.readline()
        self.assertTrue(line, proc.stderr.read().decode() if proc.poll() is not None else '')
        return json.loads(line)

    def test_loss_reordering_corruption_and_replay_do_not_block_later_frames(self):
        proc, sender = self.receiver()
        start = time.monotonic()
        replay = delayed = None
        for seq in range(55):
            tone = array.array('h', [int(1000*math.sin(2*math.pi*440*(seq*960+i)/48000))
                                    for i in range(960) for _ in range(2)])
            packet = self.encoder.packet(tone.tobytes(), seq)
            if seq == 0:
                replay = packet
            if seq == 16:
                delayed = packet
            elif seq not in (8, 24, 25):
                sender.send(packet)
            if seq == 19:
                sender.send(delayed)
            if seq == 30:
                sender.send(replay)
                damaged = bytearray(packet)
                damaged[-1] ^= 1  # Invalid authenticated tag.
                sender.send(damaged)
                damaged[4] ^= 1  # A packet from another session.
                sender.send(damaged)
            time.sleep(max(0, start + (seq+1)*.02 - time.monotonic()))
        report = self.report(proc)
        self.assertGreaterEqual(report['received'], 40)
        self.assertGreaterEqual(report['played'], 40)
        self.assertGreaterEqual(report['redundant'], 3)
        self.assertGreaterEqual(report['concealed'], 1)
        self.assertGreaterEqual(report['late'], 1)
        self.assertGreaterEqual(report['invalid'], 2)
        self.assertEqual(report['underruns'], 0)
        self.assertEqual(report['resync'], 0)

    def test_unauthenticated_packets_cannot_start_playback(self):
        proc, sender = self.receiver()
        packet = self.encoder.packet(bytes(3840), 0)
        damaged = bytearray(packet)
        damaged[-1] ^= 1
        for _ in range(10):
            sender.send(damaged)
        report = self.report(proc)
        self.assertEqual(report['received'], 0)
        self.assertEqual(report['played'], 0)
        self.assertEqual(report['invalid'], 10)

    def test_ssh_eof_stops_receiver_even_while_datagrams_continue(self):
        proc, sender = self.receiver()
        sender.send(self.encoder.packet(bytes(3840), 0))
        proc.stdin.close()
        self.assertEqual(proc.wait(timeout=1), 0)

    def test_sender_burst_is_rebased_instead_of_leaving_a_permanent_backlog(self):
        proc, sender = self.receiver()
        first = self.encoder.packet(bytes(3840), 0)
        sender.send(first)
        start = time.monotonic()
        # A scheduling pause/startup burst must not add 200 ms forever.
        for index, seq in enumerate(range(10, 65)):
            sender.send(self.encoder.packet(bytes(3840), seq))
            if seq == 20:
                sender.send(first)  # An old packet cannot move the clock back.
            time.sleep(max(0, start+(index+1)*.02-time.monotonic()))
        report = self.report(proc)
        self.assertEqual(report['resync'], 1)
        self.assertGreaterEqual(report['played'], 40)
        self.assertGreaterEqual(report['late'], 1)
        self.assertLessEqual(report['concealed'], 1)
        self.assertEqual(report['underruns'], 0)

    def test_sequence_cannot_repeat_or_wrap_and_packet_fits_mtu(self):
        first = self.encoder.packet(bytes(3840), 0)
        second = self.encoder.packet(bytes(3840), 1)
        self.assertEqual(first[:4], b'AVP1')
        self.assertEqual(len(first), 196)
        self.assertEqual(len(second), 356)
        for seq in (1, 0, 2**32, -1):
            with self.subTest(seq=seq), self.assertRaises(ValueError):
                self.encoder.packet(bytes(3840), seq)

    def test_decrypted_stereo_pcm_reaches_alsa_with_both_channels(self):
        with tempfile.TemporaryDirectory(prefix='alice-opus-pcm-') as temp:
            root = Path(temp)
            (root/'asound.conf').write_text(
                'pcm.null { type null }\n'
                f'pcm.packet_test {{ type file slave.pcm "null" '
                f'file "{root}/output.raw" format "raw" }}\n')
            proc, sender = self.receiver({'ALSA_CONFIG_PATH': str(root/'asound.conf'),
                                          'ALICE_PLAYBACK_DEVICE': 'packet_test'})
            start = time.monotonic()
            for seq in range(35):
                tone = array.array('h', [int(1000*math.sin(2*math.pi*hz*(seq*960+i)/48000))
                                        for i in range(960) for hz in (440, 880)])
                sender.send(self.encoder.packet(tone.tobytes(), seq))
                time.sleep(max(0, start+(seq+1)*.02-time.monotonic()))
            proc.stdin.close()
            self.assertEqual(proc.wait(timeout=1), 0)
            samples = array.array('h')
            samples.frombytes((root/'output.raw').read_bytes())
            self.assertGreater(len(samples), 48000)
            # Skip codec startup; measure only self-generated synthetic samples.
            channels = [samples[9600::2], samples[9601::2]]
            def energy(values, hz):
                real = sum(v*math.cos(2*math.pi*hz*i/48000) for i,v in enumerate(values))
                imag = sum(v*math.sin(2*math.pi*hz*i/48000) for i,v in enumerate(values))
                return real*real + imag*imag
            for values, own, other in [(channels[0], 440, 880), (channels[1], 880, 440)]:
                self.assertGreater(math.sqrt(sum(v*v for v in values)/len(values)), 400)
                self.assertGreater(energy(values, own), 4*energy(values, other))


if __name__ == '__main__':
    unittest.main()
