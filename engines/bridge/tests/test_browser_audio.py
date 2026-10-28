"""Restored stream mute must not make a freshly opened call silently deaf."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bridge import MIC_SOURCE, SPEAKER, STATE_DIR, prepare_browser_audio


class BrowserAudio(unittest.TestCase):
    def fixture(self, source_muted=False, wrong_routes=False):
        captures = [
            {'index': 31, 'source': 999 if wrong_routes else 11,
             'mute': True, 'corked': False, 'properties': {
                 'application.name': 'Chromium input', 'application.process.id': '101'}},
            {'index': 32, 'source': 999, 'mute': True, 'corked': False,
             'properties': {'application.name': 'Chromium input',
                            'application.process.id': '102'}},
        ]
        outputs = [{'index': 41, 'sink': 999 if wrong_routes else 22, 'mute': True,
                    'properties': {'application.name': 'Chromium',
                                   'application.process.id': '101'}}]
        metadata = {
            'source-outputs': captures,
            'sink-inputs': outputs,
            'sources': [{'index': 11, 'name': MIC_SOURCE, 'mute': source_muted}],
            'sinks': [{'index': 22, 'name': SPEAKER, 'mute': False}],
        }
        mutations = []

        def command(*args):
            if args[:4] == ('pactl', '-f', 'json', 'list'):
                return json.dumps(metadata[args[4]]).encode()
            mutations.append(args)
            return b''

        def cmdline(path):
            if str(path) == '/proc/101/cmdline':
                return b'chromium\0--user-data-dir=' + str(STATE_DIR / 'browser-profile').encode()
            return b'chromium\0--user-data-dir=/some-other-profile'

        self.enterContext(patch('bridge.command', side_effect=command))
        self.enterContext(patch.object(Path, 'read_bytes', autospec=True, side_effect=cmdline))
        return mutations

    def test_restored_mute_is_removed_only_from_the_dedicated_browser(self):
        changes = self.fixture()
        self.assertTrue(prepare_browser_audio())
        self.assertEqual(changes, [
            ('pactl', 'set-source-output-mute', '31', '0'),
            ('pactl', 'set-sink-input-mute', '41', '0'),
        ])

    def test_recording_and_playback_are_routed_back_to_the_station(self):
        changes = self.fixture(wrong_routes=True)
        self.assertTrue(prepare_browser_audio())
        self.assertIn(('pactl', 'move-source-output', '31', MIC_SOURCE), changes)
        self.assertIn(('pactl', 'move-sink-input', '41', SPEAKER), changes)
        self.assertFalse(any('32' in change for change in changes))

    def test_global_microphone_mute_is_preserved_and_opening_fails_clearly(self):
        changes = self.fixture(source_muted=True)
        with self.assertRaisesRegex(RuntimeError, 'virtual microphone is muted'):
            prepare_browser_audio()
        self.assertEqual(changes, [])


if __name__ == '__main__':
    unittest.main()
