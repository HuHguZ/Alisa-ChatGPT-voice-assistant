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
    def fixture(self, initial):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / 'plum'
        for zone in range(1, 4):
            for index, color in enumerate(['red', 'green', 'blue']):
                path = Path(f'{root}:{color}:ring{zone}')
                path.mkdir()
                (path / 'brightness').write_text(str(initial[index]) + '\n')
        proc = subprocess.Popen(['bash', str(ROOT / 'station/indicator.sh')],
                                env={**os.environ, 'ALICE_LED_ROOT': str(root)},
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

    def test_blue_and_original_frame_restored_on_eof(self):
        proc, root = self.fixture((4, 3, 2))
        proc.stdin.write(b'B\n')
        self.assertTrue(select.select([proc.stdout], [], [], 2)[0])
        self.assertEqual(proc.stdout.readline(), b'BLUE\n')
        self.assertEqual(self.values(root), [0, 0, 160] * 3)
        proc.stdin.close()
        self.assertEqual(proc.wait(timeout=3), 0)
        self.assertEqual(self.values(root), [4, 3, 2] * 3)

    def test_red_indicator_keeps_priority(self):
        proc, root = self.fixture((100, 0, 0))
        proc.stdin.write(b'B\n')
        proc.stdin.close()
        self.assertEqual(proc.wait(timeout=3), 0)
        self.assertEqual(self.values(root), [100, 0, 0] * 3)


if __name__ == '__main__':
    unittest.main()
