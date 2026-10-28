"""Animation timing and mode selection without audio or browser access."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import indicator


class Animation(unittest.TestCase):
    def test_live_color_preference_changes_without_restarting_indicator(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(indicator,'STATE_DIR',Path(temp)):
            style=indicator.StylePreference('rainbow')
            self.assertEqual(style.current(),'rainbow')
            path=Path(temp)/'preferences.json';path.write_text('{"led":"red"}')
            self.assertEqual(style.current(),'red')
            path.write_text('{"led":"pulse-blue"}');self.assertEqual(style.current(),'pulse-blue')
            path.write_text('{"led":"invalid"}');self.assertEqual(style.current(),'pulse-blue')
            path.write_text('{"led":null}');self.assertEqual(style.current(),'pulse-blue')
    def test_rainbow_is_bounded_smooth_and_uses_three_distinct_zones(self):
        frames = [indicator.rainbow_frame(t / 10) for t in range(61)]
        for frame in frames:
            self.assertEqual(len(frame), 9)
            self.assertTrue(all(0 <= value <= 160 for value in frame))
            self.assertEqual(len({frame[:3], frame[3:6], frame[6:]}), 3)
        self.assertGreater(len(set(frames)), 50)
        self.assertEqual(frames[0], frames[-1])
        self.assertLessEqual(max(abs(a-b) for left, right in zip(frames, frames[1:])
                                 for a, b in zip(left, right)), 32)

    def test_only_native_mode_releases_the_overlay(self):
        self.assertEqual(indicator.light_command(False, True, 1, 'rainbow'), b'0\n')
        self.assertEqual(indicator.light_command(True, False, 1, 'rainbow'),
                         b'F 0 0 0 0 0 0 0 0 0\n')
        self.assertEqual(indicator.light_command(True, True, 1, 'blue'), b'F 0 0 160 0 0 160 0 0 160\n')
        command = indicator.light_command(True, True, 1, 'rainbow').split()
        self.assertEqual(command[0], b'F')
        self.assertEqual(len(command), 10)
        with self.assertRaises(ValueError):
            indicator.light_command(False, False, 1, 'unknown')

    def test_opening_mode_uses_fresh_living_controller_state(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(indicator, 'STATE_DIR', Path(temp)), \
             patch.object(Path, 'read_bytes', return_value=str(indicator.ROOT/'voice.py').encode()):
            path = Path(temp)/'voice.json'
            path.write_text(json.dumps({'pid': os.getpid(), 'phase': 'running', 'native_events_connected': True,
                                        'native_agent': {'mode': 'chat'}}))
            self.assertTrue(indicator.chat_mode({}))
            os.utime(path, (time.time()-10, time.time()-10))
            self.assertFalse(indicator.chat_mode({}))
            self.assertTrue(indicator.chat_mode({'native_alice_paused': True}))
            path.write_text('{}')
            self.assertFalse(indicator.chat_mode({}))
