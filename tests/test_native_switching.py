"""Native final-event validation and early cancellation without recording audio."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from native_control import byte_field, varint, recognition_event, fields, cancel_frame, pause_frame
from voice import EarlyCancel


def integer(field, value):
    return varint(field * 8) + varint(value)


def event(final=True, busy=True, matching_text=True):
    query = 'открой чат'.encode()
    recognition = byte_field(1, query) + integer(2, 1) + integer(4, int(final))
    state = (integer(1, 3 if busy else 1) +
             byte_field(2, query if matching_text else b'other') +
             byte_field(7, b'01234567-1234-5678-9abc-0123456789ab') +
             byte_field(15, recognition))
    return byte_field(252, state) + integer(253, 3)


class NativeEvents(unittest.TestCase):
    def test_cancel_keeps_dialogue_action_separate_from_music(self):
        frame = cancel_frame()
        self.assertEqual(int.from_bytes(frame[:4], 'little'), len(frame) - 4)
        envelope = fields(frame[4:])
        self.assertEqual(envelope[253], 210)
        method = fields(envelope[252])
        self.assertEqual(method[7], b'AliceCapability')
        self.assertEqual(fields(method[8])[1], 3)

    def test_music_command_is_pause_and_not_toggle_or_resume(self):
        frame = pause_frame()
        self.assertEqual(int.from_bytes(frame[:4], 'little'), len(frame) - 4)
        envelope = fields(frame[4:])
        self.assertEqual(envelope[253], 210)
        remoting = fields(envelope[252])
        self.assertEqual(remoting[7], b'PlaybackControlCapability')
        method = fields(remoting[13])
        self.assertEqual(method[1], 1)
        self.assertEqual(method[7], 0)
        source = fields(method[5])
        self.assertEqual([source[1], source[2], source[3]], [14, 3, 1])
        self.assertEqual(source[6], b'ChatGPTVoice')
        self.assertEqual(len(source[4]), 36)

    def test_only_final_matching_native_query_is_accepted(self):
        self.assertEqual(recognition_event(event()),
                         ('01234567-1234-5678-9abc-0123456789ab', 'открой чат'))
        for options in [{'final': False}, {'busy': False}, {'matching_text': False}]:
            self.assertIsNone(recognition_event(event(**options)))

    def test_truncated_or_invalid_envelopes_are_ignored(self):
        valid = event()
        for count in range(1, len(valid)):
            self.assertIsNone(recognition_event(valid[:count]))
        for malformed in [b'', b'\x00', b'\xff' * 20, b'\x0a\xff\xff\xff\x7f']:
            self.assertIsNone(recognition_event(malformed))


class EarlyCancellation(unittest.TestCase):
    def test_wake_word_and_ordinary_alice_queries_remain_untouched(self):
        early = EarlyCancel()
        for text in ['алиса', 'открой чат', 'алиса какая погода', 'алиса открой',
                     'алиса не открывай чат']:
            self.assertFalse(early.consume(text, now=1))
            self.assertFalse(early.consume(text, now=2))

    def test_only_stable_complete_opening_can_cancel_once_per_utterance(self):
        early = EarlyCancel()
        self.assertFalse(early.consume('алиса открой чат', now=1))
        self.assertFalse(early.consume('алиса открой чат', now=1.1))
        self.assertFalse(early.consume('алиса открой чат погоды', now=1.2))
        self.assertFalse(early.consume('алиса открой чат', now=2))
        self.assertTrue(early.consume('алиса открой чат', now=2.3))
        self.assertFalse(early.consume('алиса открой чат', now=3))
        early.reset()
        self.assertFalse(early.consume('алиса создай новый чат', now=4))
        self.assertTrue(early.consume('алиса создай новый чат', now=4.3))
        early.reset()
        self.assertFalse(early.consume('алиса закрой чат', now=5))
        self.assertFalse(early.consume('алиса закрой чат', now=6))


if __name__ == '__main__':
    unittest.main()
