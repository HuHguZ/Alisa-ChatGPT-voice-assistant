"""Measure the physical audio round trip with a brief tone, keeping PCM in RAM."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bridge import MIC_SOURCE, SPEAKER


async def measure():
    capture = await asyncio.create_subprocess_exec(
        'pacat', '--record', '--raw', '--format=s16le', '--rate=48000',
        '--channels=1', '--latency-msec=20', f'--device={MIC_SOURCE}',
        '--client-name=AliceLatencyVerification', stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    detections = []
    sent_at = None

    async def read():
        while True:
            raw = await capture.stdout.readexactly(1920)
            samples = np.frombuffer(raw, dtype='<i2').astype(float) / 32768
            energy = np.abs(np.fft.rfft(samples * np.hanning(len(samples)))) ** 2
            frequency = np.fft.rfftfreq(len(samples), 1 / 48000)
            fraction = float(energy[abs(frequency - 990) < 35].sum() /
                             max(energy[frequency > 100].sum(), 1e-12))
            level = float(np.sqrt(np.mean(samples * samples)))
            if sent_at is not None and fraction > .55 and level > .0005:
                detections.append((time.monotonic() - sent_at, fraction))

    reader = asyncio.create_task(read())
    try:
        await asyncio.sleep(.5)
        t = np.arange(9600) / 48000
        tone = (np.sin(2 * np.pi * 990 * t) * .06 * np.hanning(len(t)) * 32767).astype('<i2')
        playback = await asyncio.create_subprocess_exec(
            'pacat', '--playback', '--raw', '--format=s16le', '--rate=48000',
            '--channels=1', '--latency-msec=20', f'--device={SPEAKER}',
            '--client-name=AliceLatencyVerification', stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        sent_at = time.monotonic()
        await playback.communicate(tone.tobytes())
        await asyncio.sleep(5)
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        capture.terminate()
        await capture.wait()
    return {'raw_audio_saved': False,
            'method': 'Linux output -> station speaker -> microphones -> Linux input',
            'first_detection_seconds': round(detections[0][0], 4) if detections else None,
            'detection_count': len(detections),
            'max_tone_fraction': round(max((d[1] for d in detections), default=0), 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'latency-current-test.json')
    args = parser.parse_args()
    report = asyncio.run(measure())
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    args.output.chmod(0o600)
    print(json.dumps(report), flush=True)
    return 0 if report['detection_count'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
