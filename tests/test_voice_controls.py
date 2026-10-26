"""LED restoration checks without account or audio access."""
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
class Indicator(unittest.TestCase):
    def fixture(self, initial, triggers=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / 'plum'
        gpio = root.parent / 'gpio'
        gpio.write_text(' gpio-505 (                    |mute-switch         ) in  hi\n')
        for zone in range(1, 4):
            for index, color in enumerate(['red', 'green', 'blue']):
                path = Path(f'{root}:{color}:ring{zone}')
                path.mkdir()
                (path / 'brightness').write_text(str(initial[index]) + '\n')
                trigger = triggers[(zone - 1) * 3 + index] if triggers else 'led_ring_fb'
                (path / 'trigger').write_text(f'[{trigger}] none led_ring_fb heartbeat\n')
        proc = subprocess.Popen(['bash', str(ROOT / 'station/indicator.sh')],
                                env={**os.environ, 'ALICE_LED_ROOT': str(root),
                                     'ALICE_MUTE_GPIO_STATUS': str(gpio)},
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, bufsize=0)
        self.addCleanup(self.cleanup_process, proc)
        return proc, root

    @staticmethod
    def cleanup_process(proc):
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        for stream in [proc.stdin, proc.stdout, proc.stderr]:
            stream.close()

    @staticmethod
    def values(root):
        return [int(Path(f'{root}:{c}:ring{z}/brightness').read_text())
                for z in range(1, 4) for c in ['red', 'green', 'blue']]

    @staticmethod
    def trigger_values(root):
        result = []
        for zone in range(1, 4):
            for color in ['red', 'green', 'blue']:
                value = Path(f'{root}:{color}:ring{zone}/trigger').read_text().strip()
                result.append(value.split('[', 1)[1].split(']', 1)[0] if '[' in value else value)
        return result

    @staticmethod
    def detach_triggers(root):
        # Model the kernel side effect of brightness=0, absent from regular
        # fixture files. Restoring a color alone must not pass this regression.
        for zone in range(1, 4):
            for color in ['red', 'green', 'blue']:
                Path(f'{root}:{color}:ring{zone}/trigger').write_text('[none] led_ring_fb heartbeat\n')

    def command(self, proc, command, expected):
        proc.stdin.write(command + b'\n')
        self.assertTrue(select.select([proc.stdout], [], [], 2)[0])
        self.assertEqual(proc.stdout.readline(), expected + b'\n')

    def test_blue_and_original_frame_restored_on_eof(self):
        proc, root = self.fixture((4, 3, 2))
        proc.stdin.write(b'B\n')
        self.assertTrue(select.select([proc.stdout], [], [], 2)[0])
        self.assertEqual(proc.stdout.readline(), b'BLUE\n')
        self.assertEqual(self.values(root), [0, 0, 160] * 3)
        self.detach_triggers(root)
        proc.stdin.close()
        self.assertEqual(proc.wait(timeout=3), 0)
        self.assertEqual(self.values(root), [4, 3, 2] * 3)
        self.assertEqual(self.trigger_values(root), ['led_ring_fb'] * 9)

    def test_idle_release_restores_animation_after_repeated_chat_cycles(self):
        proc, root = self.fixture((0, 0, 0))
        for _ in range(3):
            self.command(proc, b'B', b'BLUE')
            self.detach_triggers(root)
            self.command(proc, b'0', b'STOCK')
            self.assertEqual(self.values(root), [0] * 9)
            self.assertEqual(self.trigger_values(root), ['led_ring_fb'] * 9)

    def test_lease_timeout_restores_animation_without_an_idle_command(self):
        proc, root = self.fixture((0, 0, 0))
        self.command(proc, b'B', b'BLUE')
        self.detach_triggers(root)
        self.assertEqual(proc.wait(timeout=3), 0)
        self.assertEqual(self.trigger_values(root), ['led_ring_fb'] * 9)

    def test_restore_preserves_each_original_trigger_and_a_new_owner(self):
        original = ['none', 'heartbeat', 'led_ring_fb'] * 3
        proc, root = self.fixture((0, 0, 0), triggers=original)
        self.command(proc, b'B', b'BLUE')
        self.detach_triggers(root)
        Path(f'{root}:blue:ring3/trigger').write_text('none led_ring_fb [heartbeat]\n')
        self.command(proc, b'0', b'STOCK')
        self.assertEqual(self.trigger_values(root), original[:-1] + ['heartbeat'])
        self.assertEqual(self.values(root)[-1], 160)

    def test_red_preemption_restores_animation_without_overwriting_the_warning(self):
        proc, root = self.fixture((0, 0, 0))
        self.command(proc, b'B', b'BLUE')
        self.detach_triggers(root)
        for zone in range(1, 4):
            for color, value in [('red', 100), ('green', 0), ('blue', 0)]:
                Path(f'{root}:{color}:ring{zone}/brightness').write_text(str(value) + '\n')
        self.command(proc, b'B', b'STOCK')
        self.assertEqual(self.values(root), [100, 0, 0] * 3)
        self.assertEqual(self.trigger_values(root), ['led_ring_fb'] * 9)

    def test_red_indicator_keeps_priority(self):
        proc, root = self.fixture((100, 0, 0))
        proc.stdin.write(b'B\n')
        proc.stdin.close()
        self.assertEqual(proc.wait(timeout=3), 0)
        self.assertEqual(self.values(root), [100, 0, 0] * 3)

    def test_rainbow_can_advance_from_its_own_red_zone_and_restore_the_original(self):
        proc, root = self.fixture((4, 3, 2))
        self.command(proc, b'F 160 0 0 0 160 0 0 0 160', b'FRAME')
        self.assertEqual(self.values(root), [160, 0, 0, 0, 160, 0, 0, 0, 160])
        self.assertEqual(self.trigger_values(root), ['none'] * 9)
        self.command(proc, b'F 160 80 0 0 160 80 80 0 160', b'FRAME')
        self.assertEqual(self.values(root), [160, 80, 0, 0, 160, 80, 80, 0, 160])
        self.command(proc, b'0', b'STOCK')
        self.assertEqual(self.values(root), [4, 3, 2] * 3)
        self.assertEqual(self.trigger_values(root), ['led_ring_fb'] * 9)

    def test_silent_chat_keeps_native_animations_detached_until_exit(self):
        proc, root = self.fixture((4, 3, 2))
        self.command(proc, b'F 0 0 0 0 0 0 0 0 0', b'FRAME')
        self.assertEqual(self.values(root), [0] * 9)
        self.assertEqual(self.trigger_values(root), ['none'] * 9)
        self.command(proc, b'0', b'STOCK')
        self.assertEqual(self.trigger_values(root), ['led_ring_fb'] * 9)

    def test_hardware_mute_releases_a_dark_overlay_and_blocks_reacquisition(self):
        proc, root = self.fixture((0, 0, 0))
        self.command(proc, b'F 0 0 0 0 0 0 0 0 0', b'FRAME')
        (root.parent/'gpio').write_text(' gpio-505 ( |mute-switch ) in lo\n')
        self.command(proc, b'F 160 0 0 0 160 0 0 0 160', b'STOCK')
        self.assertEqual(self.trigger_values(root), ['led_ring_fb'] * 9)
        proc.stdin.write(b'F 160 0 0 0 160 0 0 0 160\n')
        proc.stdin.close()
        self.assertEqual(proc.wait(timeout=3), 0)
        self.assertEqual(self.values(root), [0] * 9)
        self.assertEqual(self.trigger_values(root), ['led_ring_fb'] * 9)

    def test_missing_mute_state_releases_native_control(self):
        proc, root = self.fixture((0, 0, 0))
        self.command(proc, b'B', b'BLUE')
        (root.parent/'gpio').unlink()
        self.command(proc, b'F 0 0 0 0 0 0 0 0 0', b'STOCK')
        self.assertEqual(self.trigger_values(root), ['led_ring_fb'] * 9)

    def test_malformed_frames_cannot_touch_the_leds(self):
        for values in ['0 0 0', '256 0 0 0 0 0 0 0 0', '-1 0 0 0 0 0 0 0 0',
                       '01 0 0 0 0 0 0 0 0', '* 0 0 0 0 0 0 0 0']:
            with self.subTest(values=values):
                proc, root = self.fixture((4, 3, 2))
                proc.stdin.write(('F ' + values + '\n').encode())
                proc.stdin.close()
                self.assertEqual(proc.wait(timeout=3), 0)
                self.assertEqual(self.values(root), [4, 3, 2] * 3)
                self.assertEqual(self.trigger_values(root), ['led_ring_fb'] * 9)


if __name__ == '__main__':
    unittest.main()
