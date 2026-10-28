"""Keep late native music out of a ChatGPT call, leaving idle Alice alone."""
import asyncio


class PlaybackGuard:
    def __init__(self, active, pause, interval=.5):
        self.active = active
        self.pause = pause
        self.interval = interval
        self.error = None

    async def run(self):
        while True:
            if self.active():
                try:
                    await self.pause()
                    self.error = None
                except Exception as exc:
                    self.error = str(exc)
            else:
                self.error = None
            await asyncio.sleep(self.interval)
