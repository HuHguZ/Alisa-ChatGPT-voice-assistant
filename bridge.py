#!/usr/bin/env python3
"""Temporary SSH audio bridge between a rooted Lite 2 and desktop PipeWire."""
from __future__ import annotations

import argparse
import array
import asyncio
import contextlib
from collections import deque
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from audio_gain import SpeechGain
from microphone_transport import MicrophoneTransport, limit_input_pipe

ROOT = Path(__file__).resolve().parent
STATE_DIR = Path.home() / '.local/state/alice-voice-bridge'
STATE_FILE = STATE_DIR / 'state.json'
BROWSER_STATE = STATE_DIR / 'browser.json'
REMOTE = '/data/opt/alice-voice'
MIC_BACKEND = 'alice_station_capture'
MIC_SOURCE = 'alice_station_mic'
SPEAKER = 'alice_station_speaker'
MICROPHONE_MODE = os.environ.get('ALICE_MICROPHONE_SOURCE', 'raw')
if MICROPHONE_MODE not in ('raw', 'vqe'):
    raise ValueError('ALICE_MICROPHONE_SOURCE must be raw or vqe')
MICROPHONE_RATE = 16000 if MICROPHONE_MODE == 'vqe' else 48000
# Undocumented float32/channel enum ABI: refuse an unverified library build.
VQE_LIBRARY_SHA256 = 'fa7ad1544fe9ddfccedcf15acc031768345b0b8c7333bc1c0b47bc476dd4152f'


def command(*args: str, timeout: float = 15, data: bytes | None = None) -> bytes:
    result = subprocess.run(args, input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace').strip() or
                           result.stdout.decode(errors='replace').strip() or
                           f'{args[0]} exited with {result.returncode}')
    return result.stdout


def ssh_args(host: str) -> list[str]:
    if not host or host.startswith('-') or any(c.isspace() for c in host):
        raise ValueError('Invalid SSH host alias')
    return ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6',
            '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2', host]


def ssh(host: str, text: str, **kwargs) -> bytes:
    return command(*ssh_args(host), text, **kwargs)


def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def alive(state: dict) -> bool:
    try:
        pid = int(state['pid'])
        return str(ROOT / 'bridge.py').encode() in Path(f'/proc/{pid}/cmdline').read_bytes()
    except (KeyError, ValueError, OSError):
        return False


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = STATE_DIR / 'state.new'
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    temp.chmod(0o600)
    temp.replace(STATE_FILE)


def stage_station(host: str) -> None:
    """Add a nonblocking capture tap to a separate, temporary launcher profile."""
    if MICROPHONE_MODE == 'vqe':
        digest = ssh(host, 'sha256sum /system/vendor/quasar/libs/libyandex_evil.so').decode().split()[0]
        if digest != VQE_LIBRARY_SHA256:
            raise RuntimeError('Unverified VQE library ABI; select ALICE_MICROPHONE_SOURCE=raw')
    stock = ssh(host, 'cat /etc/asound.conf').decode()
    launch = json.loads(ssh(host, 'cat /system/vendor/quasar/launch.json'))
    if stock.count('pcm "hw:0,1"') != 1:
        raise RuntimeError('Unexpected ALSA configuration; refusing to change the runtime profile')
    patched = 0
    for node in launch['nodes']:
        for task in node['tasks']:
            if '--platformd' not in task.get('arg', []):
                continue
            env = task.get('envs', [])
            if any(s.startswith('LD_PRELOAD=') for s in env):
                raise RuntimeError('A custom LD_PRELOAD is already configured')
            helper = 'vqe_tap' if MICROPHONE_MODE == 'vqe' else 'capture_tap'
            task['envs'] = env + [f'LD_PRELOAD={REMOTE}/{helper}.so',
                                  f'ALICE_CAPTURE_SOCKET={REMOTE}/microphone.sock',
                                  f'ALICE_NATIVE_MUTE_FILE={REMOTE}/native-muted']
            patched += 1
    if patched != 1:
        raise RuntimeError('Expected exactly one stock platformd task')
    files = {
        'launch.bridge.json': json.dumps(launch, indent=2) + '\n',
        'profile.sh': (ROOT / 'station/profile.sh').read_text(),
    }
    ssh(host, f'umask 077; mkdir -p {REMOTE}')
    for name, text in files.items():
        ssh(host, f'umask 077; cat > {REMOTE}/{name}', data=text.encode())
    for name in ['capture_tap.so', 'vqe_tap.so', 'playback_worker.so', 'native_agent.so']:
        ssh(host, f'umask 077; cat > {REMOTE}/{name}.new && mv {REMOTE}/{name}.new {REMOTE}/{name}',
            data=(ROOT / 'build' / name).read_bytes())
    ssh(host, f'chmod 700 {REMOTE}/profile.sh')
    backups = ROOT / 'backups'
    backups.mkdir(mode=0o700, exist_ok=True)
    for name, text in [('station-asound.original.conf', stock)]:
        p = backups / name
        p.write_text(text)
        p.chmod(0o600)
    print('Stock ALSA SHA-256:', hashlib.sha256(stock.encode()).hexdigest(), flush=True)


def has_shared_profile(host: str) -> bool:
    text = ssh(host, 'for p in $(pidof quasar_launcher2); do tr "\\000" " " </proc/$p/cmdline; done')
    if f'{REMOTE}/launch.bridge.json'.encode() not in text:
        return False
    # A running raw tap and a new VQE tap have different sample rates. Do not
    # reuse the launcher just because its configuration pathname is the same.
    mapped = ssh(host, 'for p in $(pidof maind); do '
                 'sed -n "/alice-voice.*tap\\.so/p" /proc/$p/maps; done')
    helper = 'vqe_tap' if MICROPHONE_MODE == 'vqe' else 'capture_tap'
    return f'{REMOTE}/{helper}.so'.encode() in mapped


def rms(data: bytes) -> float:
    samples = array.array('h')
    samples.frombytes(data[:len(data) - len(data) % 2])
    if sys.byteorder != 'little':
        samples.byteswap()
    return math.sqrt(sum(s * s for s in samples) / max(len(samples), 1)) / 32768


async def probe_station(host: str, seconds: int = 2) -> dict:
    proc = await asyncio.create_subprocess_exec(
        *ssh_args(host),
        f'exec socat -u UNIX-RECV:{REMOTE}/microphone.sock,unlink-early STDOUT',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        data = await asyncio.wait_for(proc.stdout.readexactly(seconds * MICROPHONE_RATE * 2), timeout=12)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise RuntimeError('No microphone samples arrived within 12 seconds') from exc
    except BaseException:
        proc.kill()
        await proc.wait()
        raise
    finally:
        if proc.returncode is None:
            proc.terminate()
            await proc.wait()
    return {'bytes': len(data), 'seconds': seconds, 'rate': MICROPHONE_RATE,
            'source': MICROPHONE_MODE, 'rms': round(rms(data), 6)}


async def prepare_station(host: str) -> None:
    stage_station(host)
    if not has_shared_profile(host):
        print('Starting the temporary capture-tap profile...', flush=True)
        ssh(host, f'{REMOTE}/profile.sh bridge', timeout=25)
        await asyncio.sleep(9)
    result = await probe_station(host)
    ssh(host, f'{REMOTE}/profile.sh commit')
    print('Microphone probe:', json.dumps(result), flush=True)


def load_modules() -> list[int]:
    ids = []
    definitions = [
        ('module-null-sink', f'sink_name={MIC_BACKEND}', 'rate=48000', 'channels=1',
         'channel_map=mono', 'sink_properties=device.description=Station_Microphone_Backend'),
        ('module-remap-source', f'source_name={MIC_SOURCE}', f'master={MIC_BACKEND}.monitor',
         'rate=48000', 'channels=1', 'channel_map=mono', 'master_channel_map=mono',
         'source_properties=device.description=Yandex_Station_Lite_2_Microphone'),
        ('module-null-sink', f'sink_name={SPEAKER}', 'rate=48000', 'channels=2',
         'channel_map=front-left,front-right',
         'sink_properties=device.description=Yandex_Station_Lite_2_Speaker'),
    ]
    try:
        for args in definitions:
            ids.append(int(command('pactl', 'load-module', *args).strip()))
        return ids
    except BaseException:
        unload_modules(ids)
        raise


def unload_modules(ids: list[int]) -> None:
    for module_id in reversed(ids):
        with contextlib.suppress(Exception):
            command('pactl', 'unload-module', str(module_id))


def close_browser() -> None:
    try:
        browser = json.loads(BROWSER_STATE.read_text())
        pid = int(browser['pid'])
        args = Path(f'/proc/{pid}/cmdline').read_bytes()
        if str(STATE_DIR / 'browser-profile').encode() in args:
            os.kill(pid, signal.SIGTERM)
    except (FileNotFoundError, ValueError, KeyError, ProcessLookupError):
        pass
    BROWSER_STATE.unlink(missing_ok=True)


def open_browser() -> None:
    state = read_state()
    if not alive(state) or state.get('phase') != 'running':
        raise RuntimeError('Start the audio bridge first')
    result = command(sys.executable, str(ROOT / 'voice.py'), 'open', timeout=55)
    print(result.decode(), end='')


def dedicated_browser_streams(kind: str) -> list[dict]:
    """Inspect only streams owned by the dedicated browser profile."""
    profile = str(STATE_DIR / 'browser-profile').encode()
    entries = json.loads(command('pactl', '-f', 'json', 'list', kind))
    owned = []
    for entry in entries:
        props = entry.get('properties', {})
        if 'chrom' not in props.get('application.name', '').lower():
            continue
        try:
            pid = int(props['application.process.id'])
            if profile in Path(f'/proc/{pid}/cmdline').read_bytes():
                owned.append(entry)
        except (KeyError, ValueError, OSError):
            continue
    return owned


def dedicated_browser_uses_microphone() -> bool:
    return any(not entry.get('corked')
               for entry in dedicated_browser_streams('source-outputs'))


def prepare_browser_audio() -> bool:
    """Restore dedicated stream routing and mute on an explicit chat opening.

    PulseAudio can remember a previous recording stream's mute across browser
    restarts. An open capture stream alone does not mean its samples are heard.
    Global microphone mute and other browser profiles are never changed.
    """
    capture = dedicated_browser_streams('source-outputs')
    if not capture:
        return False
    sources = json.loads(command('pactl', '-f', 'json', 'list', 'sources'))
    sinks = json.loads(command('pactl', '-f', 'json', 'list', 'sinks'))
    source = next((s for s in sources if s.get('name') == MIC_SOURCE), None)
    sink = next((s for s in sinks if s.get('name') == SPEAKER), None)
    if not source or not sink:
        raise RuntimeError('Station audio devices are unavailable')
    if source.get('mute'):
        raise RuntimeError('Station virtual microphone is muted')
    for entry in capture:
        index = str(entry['index'])
        if entry.get('source') != source['index']:
            command('pactl', 'move-source-output', index, MIC_SOURCE)
        if entry.get('mute'):
            command('pactl', 'set-source-output-mute', index, '0')
    for entry in dedicated_browser_streams('sink-inputs'):
        index = str(entry['index'])
        if entry.get('sink') != sink['index']:
            command('pactl', 'move-sink-input', index, SPEAKER)
        if entry.get('mute'):
            command('pactl', 'set-sink-input-mute', index, '0')
    return any(not entry.get('corked') for entry in capture)


async def run_bridge(host: str) -> None:
    previous = read_state()
    if alive(previous):
        raise RuntimeError(f'Bridge already running with PID {previous["pid"]}')
    for name in ['ssh', 'pactl', 'pacat']:
        if not shutil.which(name):
            raise RuntimeError(f'Missing required command: {name}')
    command('pactl', 'info')
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in [signal.SIGINT, signal.SIGTERM]:
        loop.add_signal_handler(sig, stop.set)
    modules: list[int] = []
    processes: list[asyncio.subprocess.Process] = []
    tasks: list[asyncio.Task] = []
    state = {'pid': os.getpid(), 'host': host, 'phase': 'starting', 'modules': [],
             'microphone': MIC_SOURCE, 'speaker': SPEAKER,
             'capture_bytes': 0, 'playback_bytes': 0, 'microphone_rms': 0.0,
             'started_at': time.time(), 'microphone_source': MICROPHONE_MODE,
             'microphone_rate': MICROPHONE_RATE}
    save_state(state)
    try:
        await prepare_station(host)
        if stop.is_set():
            return
        modules = load_modules()
        state['modules'] = modules

        async def spawn(*args: str):
            proc = await asyncio.create_subprocess_exec(
                *args, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            processes.append(proc)
            return proc

        capture = await spawn(*ssh_args(host),
                              f'exec socat -u UNIX-RECV:{REMOTE}/microphone.sock,unlink-early STDOUT')
        mic_player = await spawn('pacat', '--playback', '--raw', '--format=s16le',
                                 f'--rate={MICROPHONE_RATE}', '--channels=1', f'--device={MIC_BACKEND}',
                                 '--latency-msec=20', '--process-time-msec=10',
                                 '--client-name=AliceVoiceBridge',
                                 '--stream-name=StationMicrophone')
        state['microphone_pipe_bytes'] = limit_input_pipe(mic_player.stdin, MICROPHONE_RATE // 100 * 2)
        microphone = MicrophoneTransport(state, rms, sample_rate=MICROPHONE_RATE)
        speaker_monitor = await spawn('pacat', '--record', '--raw', '--format=s16le',
                                      '--rate=48000', '--channels=2',
                                      f'--device={SPEAKER}.monitor', '--latency-msec=20',
                                      '--process-time-msec=10',
                                      '--client-name=AliceVoiceBridge',
                                      '--stream-name=StationSpeaker')
        speaker = await spawn(*ssh_args(host),
                              f'export LD_PRELOAD={REMOTE}/playback_worker.so '
                              f'ALICE_PLAYBACK_WORKER=1; exec aplay --version '
                              f'--process-id-file {REMOTE}/playback.pid')
        if await asyncio.wait_for(speaker.stdout.readexactly(6), 10) != b'READY\n':
            raise RuntimeError('Station playback worker did not initialize')
        playback_queue: asyncio.Queue = asyncio.Queue(maxsize=6)
        # The window covers ALSA occupancy plus data and ACKs in transit.
        # Eight blocks give Wi-Fi jitter headroom without enlarging ALSA or
        # allowing an unbounded SSH backlog. Stale PC blocks are still dropped.
        playback_slots = asyncio.Semaphore(8)
        state['playback_window_ms'] = 160
        pending_playback = deque()
        state['playback_dropped_bytes'] = 0
        state['playback_queue_ms'] = 0
        state['playback_underruns'] = 0
        state['playback_ack_ms'] = 0
        state['playback_ack_ms_max'] = 0
        speech_gain = SpeechGain(6.0)
        state['playback_boost_db'] = 6.0
        state['playback_gain_db'] = speech_gain.gain_db
        state['playback_limited_blocks'] = 0

        async def read_playback() -> None:
            while True:
                block = await speaker_monitor.stdout.readexactly(3840)
                if playback_queue.full():
                    playback_queue.get_nowait()
                    state['playback_dropped_bytes'] += 3840
                playback_queue.put_nowait((time.monotonic(), block))
                state['playback_queue_ms'] = playback_queue.qsize() * 20

        async def write_playback() -> None:
            while True:
                await asyncio.wait_for(playback_slots.acquire(), 3)
                queued_at, block = await playback_queue.get()
                if time.monotonic() - queued_at > 0.18:
                    state['playback_dropped_bytes'] += len(block)
                    playback_slots.release()
                    continue
                block = speech_gain.process(block)
                state['playback_gain_db'] = speech_gain.gain_db
                state['playback_limited_blocks'] = speech_gain.limited_blocks
                pending_playback.append(time.monotonic())
                speaker.stdin.write(block)
                await asyncio.wait_for(speaker.stdin.drain(), 3)

        async def acknowledge_playback() -> None:
            while True:
                ack = await speaker.stdout.readexactly(1)
                if ack not in [b'K', b'U'] or not pending_playback:
                    raise RuntimeError('Invalid station playback acknowledgement')
                ack_ms = round((time.monotonic() - pending_playback.popleft()) * 1000, 1)
                state['playback_ack_ms'] = ack_ms
                state['playback_ack_ms_max'] = max(state['playback_ack_ms_max'], ack_ms)
                state['playback_underruns'] += ack == b'U'
                state['playback_bytes'] += 3840
                state['playback_queue_ms'] = playback_queue.qsize() * 20
                playback_slots.release()

        async def errors(proc, name: str) -> None:
            while line := await proc.stderr.readline():
                message = line.decode(errors='replace').strip()
                if message:
                    print(f'{name}: {message}', file=sys.stderr, flush=True)

        async def status() -> None:
            while not stop.is_set():
                state['updated_at'] = time.time()
                save_state(state)
                await asyncio.sleep(1)

        async def native_voice_mode() -> None:
            paused = False
            state['native_alice_paused'] = False
            while not stop.is_set():
                active = await asyncio.to_thread(dedicated_browser_uses_microphone)
                if active != paused:
                    paused = active
                    state['native_alice_paused'] = paused
                    print('Native Alice replies:', 'handled by station agent' if paused else 'normal', flush=True)
                await asyncio.sleep(0.5)

        streams = [asyncio.create_task(microphone.read(capture.stdout)),
                   asyncio.create_task(microphone.write(mic_player.stdin)),
                   asyncio.create_task(read_playback()),
                   asyncio.create_task(write_playback()),
                   asyncio.create_task(acknowledge_playback()),
                   asyncio.create_task(native_voice_mode())]
        stop_task = asyncio.create_task(stop.wait())
        tasks = streams + [asyncio.create_task(status()), stop_task]
        for proc, name in zip(processes, ['station capture', 'virtual microphone',
                                          'speaker monitor', 'station playback']):
            tasks.append(asyncio.create_task(errors(proc, name)))
        state['phase'] = 'running'
        save_state(state)
        # Keep the controller and indicator in the same service lifecycle.
        for label, executable, script, args in [
            ('speaking indicator', sys.executable, 'indicator.py', []),
            ('voice commands', sys.executable, 'voice.py', ['run']),
        ]:
            helper = await asyncio.create_subprocess_exec(
                executable, str(ROOT / script), *args,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE)
            processes.append(helper)
            tasks.append(asyncio.create_task(errors(helper, label)))

            async def helper_finished(proc=helper, name=label):
                code = await proc.wait()
                if not stop.is_set():
                    raise RuntimeError(f'{name} exited with status {code}')

            helper_task = asyncio.create_task(helper_finished())
            streams.append(helper_task)
            tasks.append(helper_task)
        print('Bridge running. Browser microphone:', MIC_SOURCE, flush=True)
        print('Browser speaker:', SPEAKER, flush=True)
        watched = streams + [stop_task]
        done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task is not stop_task:
                task.result()
    finally:
        state['phase'] = 'stopping'
        save_state(state)
        # End our browser session before its virtual devices disappear, so a
        # live voice call cannot silently fall back to the computer microphone.
        close_browser()
        for task in tasks:
            task.cancel()
        for proc in processes:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
        for proc in processes:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), 3)
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        await asyncio.gather(*tasks, return_exceptions=True)
        unload_modules(modules)
        print('Restoring the stock station runtime profile...', flush=True)
        try:
            ssh(host, f'{REMOTE}/profile.sh stock', timeout=25)
            state['phase'] = 'stopped'
        except Exception as exc:
            state['phase'] = 'restore_failed'
            state['error'] = str(exc)
            print(f'Restore failed: {exc}', file=sys.stderr, flush=True)
        state['modules'] = []
        state['stopped_at'] = time.time()
        save_state(state)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'status', 'restore', 'browser'])
    parser.add_argument('--host', default='alice', help='An existing SSH host alias')
    args = parser.parse_args()
    if args.action == 'start':
        asyncio.run(run_bridge(args.host))
    elif args.action == 'status':
        state = read_state()
        state['process_alive'] = alive(state)
        print(json.dumps(state, ensure_ascii=False, indent=2))
    elif args.action == 'stop':
        state = read_state()
        if alive(state):
            os.kill(int(state['pid']), signal.SIGTERM)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and alive(read_state()):
                time.sleep(0.2)
            if alive(read_state()):
                raise RuntimeError('Bridge is still stopping; inspect its log')
            print('Bridge stopped')
        else:
            unload_modules(state.get('modules', []))
            print('Bridge is not running')
    elif args.action == 'restore':
        if alive(read_state()):
            raise RuntimeError('Stop the bridge before restoring its station profile')
        ssh(args.host, f'{REMOTE}/profile.sh stock', timeout=25)
        print('Stock profile restored')
    elif args.action == 'browser':
        open_browser()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, EOFError, TimeoutError, subprocess.SubprocessError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
