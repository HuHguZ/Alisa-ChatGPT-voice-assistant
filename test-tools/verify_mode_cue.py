"""Measure the two confirmation tones at the station's microphones in memory."""
import asyncio
import contextlib
import json
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bridge import MIC_SOURCE
from mode_cue import play_closed


async def main():
    capture = await asyncio.create_subprocess_exec(
        'pacat', '--record', '--raw', '--format=s16le', '--rate=48000',
        '--channels=1', '--latency-msec=30', f'--device={MIC_SOURCE}',
        '--client-name=AliceCueVerification', stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    metrics = {880: {'fraction': 0, 'rms': 0}, 587: {'fraction': 0, 'rms': 0}}
    observations = []
    started = time.monotonic()

    async def measure():
        while True:
            raw = await capture.stdout.readexactly(4800)
            values = np.frombuffer(raw, dtype='<i2').astype(float) / 32768
            level = float(np.sqrt(np.mean(values ** 2)))
            energy = np.abs(np.fft.rfft(values * np.hanning(len(values)))) ** 2
            frequencies = np.fft.rfftfreq(len(values), 1 / 48000)
            total = max(float(energy[frequencies > 100].sum()), 1e-12)
            for frequency in metrics:
                fraction = float(energy[abs(frequencies - frequency) < 30].sum()) / total
                if fraction > metrics[frequency]['fraction'] and level > .0003:
                    metrics[frequency] = {'fraction': round(fraction, 4), 'rms': round(level, 6)}
                if fraction > .45 and level > .0003:
                    observations.append((frequency, time.monotonic() - started))

    reader = asyncio.create_task(measure())
    try:
        await asyncio.sleep(.4)
        await play_closed()
        await asyncio.sleep(.8)
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
        capture.terminate()
        await capture.wait()
    first_high = next((t for f, t in observations if f == 880), None)
    first_low = next((t for f, t in observations if f == 587), None)
    report = {'raw_audio_saved': False, 'method': 'synthetic cue -> station speaker -> station microphones',
              'tones': metrics, 'descending_order_detected':
              first_high is not None and first_low is not None and first_high < first_low}
    path = ROOT / 'mode-cue-test.json'
    path.write_text(json.dumps(report, indent=2) + '\n')
    path.chmod(0o600)
    print(json.dumps(report, indent=2))
    return 0 if report['descending_order_detected'] else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
