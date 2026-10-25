"""Late music is paused during the call, ordinary music is left alone."""
import asyncio
import contextlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playback_guard import PlaybackGuard


class Guard(unittest.IsolatedAsyncioTestCase):
    async def start(self, active, pause):
        guard = PlaybackGuard(active, pause, interval=.005)
        task = asyncio.create_task(guard.run())

        async def cleanup():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        self.addAsyncCleanup(cleanup)
        return guard

    async def test_ordinary_music_is_not_paused_when_chat_is_inactive(self):
        calls = []

        async def pause():
            calls.append(True)

        await self.start(lambda: False, pause)
        await asyncio.sleep(.03)
        self.assertEqual(calls, [])

    async def test_late_music_is_reclaimed_and_closing_releases_the_player(self):
        active, playing = True, True
        paused = asyncio.Event()

        async def pause():
            nonlocal playing
            playing = False
            paused.set()

        await self.start(lambda: active, pause)
        await asyncio.wait_for(paused.wait(), .5)
        self.assertFalse(playing)
        paused.clear()
        playing = True  # A late native response starts music after handoff.
        await asyncio.wait_for(paused.wait(), .5)
        self.assertFalse(playing)
        active = False
        playing = True
        paused.clear()
        await asyncio.sleep(.03)
        self.assertTrue(playing)
        self.assertFalse(paused.is_set())

    async def test_failed_pause_is_retried_without_stopping_the_controller(self):
        recovered = asyncio.Event()
        calls = 0

        async def pause():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError('Temporary local IPC failure')
            recovered.set()

        guard = await self.start(lambda: True, pause)
        await asyncio.wait_for(recovered.wait(), .5)
        self.assertGreaterEqual(calls, 2)
        self.assertIsNone(guard.error)


if __name__ == '__main__':
    unittest.main()
