"""Command matching and LED restoration checks without account or audio access."""
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from voice import intent, recognized_intent, MarkedCommands, resolve_command


class Commands(unittest.TestCase):
    def test_commands_and_non_commands(self):
        for phrase, action in [('Алиса, открой чат!', 'open'), ('Алиса, создай новый чат', 'new'),
                               ('Алиса, закрой чат', 'close'),
                               ('Алиса, выйди из чата, пожалуйста.', 'close')]:
            self.assertEqual(intent(phrase), action)
        for phrase in ['расскажи как открыть чат', 'в новом чате интересно',
                       'не закрывай чат', 'привет как дела', 'новый чат',
                       'открой чат', 'закрой чат', 'выйди из чата',
                       'скажи алиса открой чат', 'Алиса, не закрывай чат',
                       'Алиса, включи музыку', 'Алиса, продолжи музыку',
                       'Алиса, поставь песню', 'Алиса, включи чат',
                       'Алиса, запусти чат']:
            self.assertIsNone(intent(phrase))

    def test_marker_confidence_is_required_and_uncertain_close_can_be_accepted(self):
        result = {'text': 'алиса закрой чат', 'result': [
            {'word': 'алиса', 'conf': 1}, {'word': 'закрой', 'conf': .48},
            {'word': 'чат', 'conf': 1}]}
        self.assertEqual(recognized_intent(result), 'close')
        result['result'][0]['conf'] = .4
        self.assertIsNone(recognized_intent(result))
        result['text'] = 'закрой чат'
        self.assertIsNone(recognized_intent(result))

    def test_pause_after_marker_does_not_lose_the_command(self):
        decoder = MarkedCommands()
        marker = {'text': 'алиса', 'result': [{'word': 'алиса', 'conf': 1}]}
        tail = {'text': 'закрой чат', 'result': [
            {'word': 'закрой', 'conf': .48}, {'word': 'чат', 'conf': 1}]}
        self.assertIsNone(decoder.consume(tail, now=1))
        self.assertIsNone(decoder.consume(marker, now=2))
        self.assertEqual(decoder.consume(tail, now=3), 'close')
        self.assertIsNone(decoder.consume(tail, now=4))
        self.assertIsNone(decoder.consume(marker, now=5))
        self.assertIsNone(decoder.consume(tail, now=9))

    def test_context_resolves_only_uncertain_opening_while_chat_is_active(self):
        words = [{'word': 'алиса', 'conf': 1}, {'word': 'открой', 'conf': .75},
                 {'word': 'чат', 'conf': 1}]
        self.assertEqual(resolve_command('open', words, 'close', active=True), 'close')
        self.assertEqual(resolve_command('open', words[1:], 'close', active=True), 'close')
        self.assertEqual(resolve_command('open', words, 'close', active=False), 'open')
        self.assertEqual(resolve_command('open', words, None, active=True), 'open')
        words[1]['conf'] = .99
        self.assertEqual(resolve_command('open', words, 'close', active=True), 'open')


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
