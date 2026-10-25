"""Check audio peaks and persisted conversation identity without account data."""
import array
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from audio_gain import SpeechGain
from browser_control import Browser, conversation_url


class Gain(unittest.TestCase):
    def test_quiet_audio_gets_six_decibels_without_sample_delay(self):
        values = array.array('h', [1000, -1000] * 960)
        gain = SpeechGain()
        output = array.array('h')
        output.frombytes(gain.process(values.tobytes()))
        self.assertEqual(len(output), len(values))
        self.assertEqual(output[0], round(1000 * 10 ** (6 / 20)))
        self.assertEqual(output[1], -output[0])

    def test_full_scale_and_silence_do_not_clip_or_wrap(self):
        gain = SpeechGain()
        output = array.array('h')
        output.frombytes(gain.process(array.array('h', [32767, -32768] * 960).tobytes()))
        self.assertLessEqual(max(map(abs, output)), int(32767 * .95))
        self.assertGreater(output[0], 0)
        self.assertLess(output[1], 0)
        self.assertEqual(gain.limited_blocks, 1)
        self.assertEqual(gain.process(bytes(3840)), bytes(3840))


class Resume(unittest.TestCase):
    URL = 'https://chatgpt.com/c/01234567-1234-5678-9abc-0123456789ab'

    def test_only_conversation_identity_is_persisted_across_controller_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'conversation.json'
            with patch('browser_control.CONVERSATION', path):
                first = Browser()
                first.remember_conversation(self.URL + '?private=value#fragment')
                second = Browser()
                self.assertEqual(second.last_url, self.URL)
                self.assertNotIn('private', path.read_text())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                second.forget_conversation()
                self.assertEqual(Browser().last_url, 'https://chatgpt.com/')
                self.assertFalse(Browser().bootstrap_previous)

    def test_external_or_non_conversation_urls_are_rejected(self):
        for url in ['http://chatgpt.com/c/01234567-1234-5678-9abc-0123456789ab',
                    self.URL.replace('chatgpt.com', 'chatgpt.com.example.org'),
                    'https://chatgpt.com/?token=x', 'file:///etc/passwd', None]:
            self.assertIsNone(conversation_url(url))


if __name__ == '__main__':
    unittest.main()
