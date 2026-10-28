#!/usr/bin/env python3
"""GPT LED ownership and a speaking animation, with a remote lease."""
import asyncio
import colorsys
import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import time

from bridge import ROOT, REMOTE, SPEAKER, STATE_DIR, alive, read_state, rms, ssh, ssh_args

STATE = STATE_DIR / 'indicator.json'


def rainbow_frame(now):
    """Three phase-shifted colors, a six-second orbit and gentle breathing."""
    peak = 136 + 24 * math.sin(math.tau * (now % 2) / 2)
    phase = (now % 6) / 6
    return tuple(round(component * peak)
                 for zone in range(3)
                 for component in colorsys.hsv_to_rgb((phase + zone / 3) % 1, 1, 1))


def light_command(chat_active, speaking, now, style):
    colors={'red':(160,0,0),'green':(0,160,0),'blue':(0,0,160),'purple':(120,0,120),'white':(100,100,100),'off':(0,0,0)}
    name=style.removeprefix('pulse-')
    if style!='rainbow' and name not in colors:raise ValueError('Unknown LED style')
    if not chat_active:
        return b'0\n'
    if not speaking:
        return b'F 0 0 0 0 0 0 0 0 0\n'
    if style != 'rainbow':
        scale=.75+.25*math.sin(math.pi*now) if style.startswith('pulse-') else 1
        return ('F '+' '.join(str(round(v*scale)) for v in colors[name]*3)+'\n').encode('ascii')
    return ('F ' + ' '.join(map(str, rainbow_frame(now))) + '\n').encode('ascii')


def chat_mode(bridge):
    if bridge.get('native_alice_paused'):
        return True
    # The native agent enters CHAT while the web call is still opening.
    # A stale/dead controller must not leave the native LEDs suppressed.
    try:
        path = STATE_DIR / 'voice.json'
        voice = json.loads(path.read_text())
        process = Path(f'/proc/{int(voice["pid"])}/cmdline').read_bytes()
        return bool(time.time() - path.stat().st_mtime < 3 and
                    str(ROOT / 'voice.py').encode() in process and
                    voice.get('phase') == 'running' and
                    voice.get('native_events_connected') and
                    voice.get('native_agent', {}).get('mode') == 'chat')
    except (KeyError, TypeError, OSError, ValueError):
        return False


def save(value):
    temp = STATE.with_suffix('.new')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.chmod(0o600)
    temp.replace(STATE)

class StylePreference:
    """Read only an atomically replaced local preference, not a remote probe."""
    def __init__(self,style):self.style=style;self.revision=None
    def current(self):
        path=STATE_DIR/'preferences.json'
        try:
            revision=path.stat().st_mtime_ns
            if revision!=self.revision:
                style=json.loads(path.read_text())['led']
                if not isinstance(style,str):raise ValueError('Invalid LED preference')
                light_command(False,False,0,style)
                self.style=style;self.revision=revision
        except (OSError,ValueError,KeyError,TypeError):pass
        return self.style


async def run():
    style = os.environ.get('ALICE_LED_STYLE', 'rainbow')
    light_command(False, False, 0, style)  # Validate before starting remote helpers.
    preference=StylePreference(style)
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
            chat_active = chat_mode(bridge)
            style=preference.current()
            speaking = bool(chat_active and time.monotonic() - last_audio < .4)
            control.stdin.write(light_command(chat_active, speaking, time.monotonic(), style))
            await asyncio.wait_for(control.stdin.drain(), 2)
            save({'pid': os.getpid(), 'phase': 'running', 'speaking': speaking, 'style': style,
                  'chat_active': chat_active,
                  'output_rms': round(level, 6), 'updated_at': time.time()})
            await asyncio.sleep(.1)
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
