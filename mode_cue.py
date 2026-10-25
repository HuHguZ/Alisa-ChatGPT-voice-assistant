"""Brief descending confirmation, sent only after native Alice is restored."""
import array
import asyncio
import math
import sys

from bridge import SPEAKER


def closed_pcm():
    rate = 48000
    result = array.array('h')
    for frequency, duration in [(880, .09), (0, .04), (587.33, .13), (0, .06)]:
        frames = round(rate * duration)
        for index in range(frames):
            fade = max(0, min(1, index / 384, (frames - 1 - index) / 384))
            value = round(32767 * .12 * fade * math.sin(2 * math.pi * frequency * index / rate))
            result.extend([value, value])
    if sys.byteorder != 'little':
        result.byteswap()
    return result.tobytes()


async def play_closed():
    player = await asyncio.create_subprocess_exec(
        'pacat', '--playback', '--raw', '--format=s16le', '--rate=48000',
        '--channels=2', '--latency-msec=30', f'--device={SPEAKER}',
        '--client-name=AliceModeCue', stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        await asyncio.wait_for(player.communicate(closed_pcm()), 3)
        if player.returncode:
            raise RuntimeError('Mode confirmation could not be played')
    finally:
        if player.returncode is None:
            player.kill()
            await player.wait()
