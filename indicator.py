#!/usr/bin/env python3
"""Temporary blue speaking light, controlled by PCM activity, with a lease."""
import asyncio
import contextlib
import fcntl
import json
import os
from pathlib import Path
import signal
import time

from bridge import ROOT, REMOTE, SPEAKER, STATE_DIR, alive, read_state, rms, ssh, ssh_args

STATE = STATE_DIR / 'indicator.json'


def save(value):
    temp = STATE.with_suffix('.new')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.chmod(0o600)
    temp.replace(STATE)


async def run():
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = (STATE_DIR / 'indicator.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    bridge = read_state()
    if not alive(bridge) or bridge.get('phase') != 'running':
        raise RuntimeError('Start the bridge first')
    host = bridge['host']
    await asyncio.to_thread(ssh, host, f'umask 077; cat > {REMOTE}/indicator.sh',
                            data=(ROOT / 'station/indicator.sh').read_bytes())
    stop = asyncio.Event()
    for sig in [signal.SIGTERM, signal.SIGINT]:
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    control = await asyncio.create_subprocess_exec(
        *ssh_args(host), f'exec sh {REMOTE}/indicator.sh',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE)
    capture = await asyncio.create_subprocess_exec(
        'pacat', '--record', '--raw', '--format=s16le', '--rate=48000', '--channels=2',
        '--latency-msec=30', f'--device={SPEAKER}.monitor',
        '--client-name=AliceSpeakingIndicator', stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    last_audio = 0.0
    level = 0.0

    async def audio():
        nonlocal last_audio, level
        while True:
            block = await capture.stdout.readexactly(3840)
            level = rms(block)
            if level > .0012:
                last_audio = time.monotonic()

    reader = asyncio.create_task(audio())
    try:
        while not stop.is_set():
            bridge = read_state()
            if not alive(bridge) or bridge.get('phase') != 'running':
                break
            if reader.done():
                reader.result()
            speaking = bool(bridge.get('native_alice_paused') and
                            time.monotonic() - last_audio < .4)
            control.stdin.write(b'B\n' if speaking else b'0\n')
            await asyncio.wait_for(control.stdin.drain(), 2)
            save({'pid': os.getpid(), 'phase': 'running', 'speaking': speaking,
                  'output_rms': round(level, 6), 'updated_at': time.time()})
            await asyncio.sleep(.15)
    finally:
        reader.cancel()
        with contextlib.suppress(Exception):
            control.stdin.write(b'0\n')
            await control.stdin.drain()
        control.stdin.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(control.wait(), 3)
        for proc in [capture, control]:
            if proc.returncode is None:
                proc.terminate()
                await proc.wait()
        await asyncio.gather(reader, return_exceptions=True)
        save({'pid': os.getpid(), 'phase': 'stopped', 'speaking': False})


if __name__ == '__main__':
    asyncio.run(run())
