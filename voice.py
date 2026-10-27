#!/usr/bin/env python3
"""Browser voice controller driven by the station-native command agent."""
import argparse
import asyncio
import contextlib
import fcntl
import json
import os
from pathlib import Path
import signal
import time

from bridge import ROOT, STATE_DIR, REMOTE, AUDIO_FOCUS_MODE, alive, read_state, dedicated_browser_uses_microphone, prepare_browser_audio, ssh_args
from browser_control import Browser
from mode_cue import play_closed
from playback_guard import PlaybackGuard

SOCKET = STATE_DIR / 'voice.sock'
STATE = STATE_DIR / 'voice.json'
async def client(action):
    for attempt in range(60):
        try:
            reader, writer = await asyncio.open_unix_connection(str(SOCKET))
            break
        except (FileNotFoundError, ConnectionRefusedError):
            bridge = read_state()
            if not alive(bridge) or bridge.get('phase') not in ['starting', 'running']:
                raise RuntimeError('Start the audio bridge first') from None
            await asyncio.sleep(.2)
    else:
        raise RuntimeError('The voice controller has not started')
    writer.write(json.dumps({'action': action}).encode() + b'\n')
    await writer.drain()
    result = json.loads(await asyncio.wait_for(reader.readline(), 50))
    writer.close()
    await writer.wait_closed()
    print(json.dumps(result, ensure_ascii=False, indent=2))


async def run():
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = (STATE_DIR / 'voice.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return
    browser = Browser()
    serial = asyncio.Lock()
    stop = asyncio.Event()
    for sig in [signal.SIGTERM, signal.SIGINT]:
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    status = {'pid': os.getpid(), 'phase': 'running', 'last_action': None,
              'successful_actions': 0, 'voice_commands': 0,
              'recognizer': 'yandex-native', 'recognition_location': 'station',
              'recognition_service': 'Yandex cloud'}
    quiet_until = 0.0
    native_proc = None
    native_ready = asyncio.Event()
    focus_ready = asyncio.Event()
    focus_error = asyncio.Event()
    opening_chat = False
    native_chat_mode = False
    spoken_action = None
    last_command_at = 0.0
    voice_missing_since = None

    def save():
        temp = STATE.with_suffix('.new')
        temp.write_text(json.dumps(status, ensure_ascii=False, indent=2) + '\n')
        temp.chmod(0o600)
        temp.replace(STATE)

    async def send_native(command):
        await asyncio.wait_for(native_ready.wait(), 12)
        if not native_proc or native_proc.returncode is not None:
            raise RuntimeError('The station command agent is unavailable')
        native_proc.stdin.write((command + '\n').encode())
        await asyncio.wait_for(native_proc.stdin.drain(), 1)

    async def set_mode(chat):
        nonlocal native_chat_mode
        if chat:
            focus_ready.clear()
            focus_error.clear()
        await send_native('CHAT' if chat else 'IDLE')
        native_chat_mode = chat
        if chat and AUDIO_FOCUS_MODE == 'native':
            ready = asyncio.create_task(focus_ready.wait())
            failed = asyncio.create_task(focus_error.wait())
            try:
                done, _ = await asyncio.wait([ready, failed], timeout=6,
                                             return_when=asyncio.FIRST_COMPLETED)
                if not done or focus_error.is_set() or not focus_ready.is_set():
                    raise RuntimeError('Station did not confirm native DIALOG audio focus')
                status.pop('audio_focus_error', None)
            finally:
                ready.cancel()
                failed.cancel()
                await asyncio.gather(ready, failed, return_exceptions=True)
        if not chat:
            focus_ready.clear()

    async def cancel_native():
        await send_native('CANCEL')
        status['native_dialogue_cancels'] = status.get('native_dialogue_cancels', 0) + 1

    async def pause_native():
        await send_native('PAUSE')
        status['native_music_pauses'] = status.get('native_music_pauses', 0) + 1

    async def start_voice(action, native_cancelled=False):
        nonlocal opening_chat
        if not native_cancelled:
            await cancel_native()
        await pause_native()
        opening_chat = True
        try:
            await set_mode(True)
            if action == 'new':
                await browser.close()
                browser.forget_conversation()
                await browser.launch()
            else:
                await browser.launch()
                await browser.resume_previous()
            for _ in range(30):
                if await asyncio.to_thread(prepare_browser_audio):
                    return {'ok': True, 'action': action, 'voice_active': True}
                if await browser.click_voice():
                    await asyncio.sleep(1.5)
                else:
                    await asyncio.sleep(.5)
            raise RuntimeError('Voice mode did not start; inspect the controller status')
        except BaseException:
            with contextlib.suppress(Exception):
                await browser.close()
            with contextlib.suppress(Exception):
                await set_mode(False)
            raise
        finally:
            opening_chat = False

    async def execute(action, spoken=False):
        nonlocal quiet_until
        async with serial:
            if action == 'status':
                return {**status, 'voice_active': await asyncio.to_thread(dedicated_browser_uses_microphone),
                        'browser_background': browser.background,
                        'previous_conversation_saved': '/c/' in browser.last_url}
            if action == 'close':
                was_active = bool(browser.proc and browser.proc.poll() is None)
                await browser.close()
                if was_active and not spoken:
                    await cancel_native()
                await set_mode(False)
                if was_active:
                    quiet_until = time.monotonic() + 2
                    try:
                        await play_closed()
                        status['close_cues'] = status.get('close_cues', 0) + 1
                        status.pop('cue_error', None)
                    except Exception as exc:
                        status['cue_error'] = str(exc)
                result = {'ok': True, 'action': action, 'voice_active': False}
            elif action == 'inspect':
                await browser.launch()
                return {'ok': True, 'buttons': await browser.buttons(), 'health': await browser.health()}
            elif action in ['open', 'new']:
                result = await start_voice(action, native_cancelled=spoken)
            else:
                raise RuntimeError('Unknown action')
            status['last_action'] = action
            status['successful_actions'] += 1
            status['voice_commands'] += spoken
            status['last_action_at'] = time.time()
            quiet_until = time.monotonic() + 2
            status.pop('error', None)
            save()
            return result

    async def handle(reader, writer):
        try:
            request = json.loads(await asyncio.wait_for(reader.readline(), 3))
            result = await execute(request['action'])
        except Exception as exc:
            result = {'ok': False, 'error': str(exc)}
        writer.write(json.dumps(result, ensure_ascii=False).encode() + b'\n')
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    def schedule(action):
        nonlocal spoken_action, last_command_at
        if ((spoken_action and not spoken_action.done()) or
                time.monotonic() - last_command_at <= 2 or time.monotonic() < quiet_until):
            return
        last_command_at = time.monotonic()
        status['last_recognized_action'] = action
        status['command_source'] = 'native-station'
        save()

        async def dispatch():
            try:
                await execute(action, spoken=True)
            except Exception as exc:
                status['error'] = str(exc)
                save()

        spoken_action = asyncio.create_task(dispatch())

    async def watch_native():
        nonlocal native_proc
        proc = await asyncio.create_subprocess_exec(
            *ssh_args(read_state()['host']),
            f'export LD_PRELOAD={REMOTE}/native_agent.so ALICE_NATIVE_AGENT=1 '
            f'ALICE_NATIVE_FOCUS={int(AUDIO_FOCUS_MODE == "native")}; '
            f'exec aplay --version --alice-native-agent {REMOTE}/native_agent.so',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        native_proc = proc

        async def heartbeats():
            was_speaking = None
            last_ping = 0.0
            while True:
                try:
                    light = json.loads((STATE_DIR / 'indicator.json').read_text())
                    speaking = bool(light.get('speaking') and time.time() - light.get('updated_at', 0) < 1)
                except (OSError, ValueError):
                    speaking = False
                if speaking != was_speaking:
                    proc.stdin.write(b'SPEAKING\n' if speaking else b'QUIET\n')
                    was_speaking = speaking
                if time.monotonic() - last_ping >= 1:
                    proc.stdin.write(b'PING\n')
                    last_ping = time.monotonic()
                await asyncio.wait_for(proc.stdin.drain(), 1)
                await asyncio.sleep(.15)

        heartbeat = asyncio.create_task(heartbeats())
        try:
            while line := await proc.stdout.readline():
                value = json.loads(line)
                event = value.get('event')
                if event == 'ready':
                    status['native_events_connected'] = True
                    status['native_agent_pid'] = value.get('pid')
                    native_ready.set()
                    # Reading focus confirmations is this task's responsibility;
                    # never await a confirmation from inside the reader itself.
                    chat = opening_chat or bool(browser.proc and browser.proc.poll() is None)
                    await send_native('CHAT' if chat else 'IDLE')
                elif event == 'command' and value.get('action') in ['open', 'new', 'close']:
                    # The station cancels its request before emitting this
                    # event. Do not send a second stop during SDK cleanup.
                    status['native_dialogue_cancels'] = status.get('native_dialogue_cancels', 0) + 1
                    schedule(value['action'])
                elif event == 'heartbeat_timeout':
                    raise RuntimeError('The station command agent lost its heartbeat')
                if event in ['ready', 'status', 'mode', 'focus', 'focus_yielded', 'focus_error']:
                    status['native_agent'] = {key: value.get(key) for key in
                        ['pid', 'mode', 'recognition_starts', 'commands', 'speech_stops',
                         'recognition_active', 'native_state', 'native_speech_events',
                         'output_active', 'wake_blocks', 'queue_clear_requests',
                         'focus_enabled', 'focus_ready', 'focus_acquires',
                         'focus_releases', 'focus_guard_pid', 'focus_recovering',
                         'focus_yields', 'focus_reacquires']}
                    if value.get('focus_ready'):
                        focus_ready.set()
                    else:
                        focus_ready.clear()
                    if event == 'focus_error':
                        focus_error.set()
                        status['audio_focus_error'] = 'Native focus lost or activity expired'
                save()
                if heartbeat.done():
                    heartbeat.result()
            raise RuntimeError('The station command agent disconnected')
        finally:
            native_ready.clear()
            focus_ready.clear()
            focus_error.set()
            status['native_events_connected'] = False
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            if proc.returncode is None:
                with contextlib.suppress(Exception):
                    proc.stdin.write(b'IDLE\nSTOP\n')
                    await asyncio.wait_for(proc.stdin.drain(), 1)
                proc.stdin.close()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), 3)
                if proc.returncode is None:
                    proc.terminate()
                    await proc.wait()
            native_proc = None

    SOCKET.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(handle, path=str(SOCKET), limit=8192)
    SOCKET.chmod(0o600)
    native_watcher = asyncio.create_task(watch_native())
    music_guard = PlaybackGuard(
        lambda: AUDIO_FOCUS_MODE == 'pause' and (
            opening_chat or bool(browser.proc and browser.proc.poll() is None and
                                 read_state().get('native_alice_paused'))), pause_native)
    music_watcher = asyncio.create_task(music_guard.run())
    save()
    try:
        while not stop.is_set():
            bridge = read_state()
            if not alive(bridge) or bridge.get('phase') != 'running':
                break
            if native_watcher.done():
                native_watcher.result()
            if music_watcher.done():
                music_watcher.result()
            if music_guard.error:
                status['native_music_pause_error'] = music_guard.error
            else:
                status.pop('native_music_pause_error', None)
            status['audio_focus_mode'] = AUDIO_FOCUS_MODE
            if (AUDIO_FOCUS_MODE == 'native' and focus_error.is_set() and
                    native_chat_mode and not serial.locked()):
                await execute('close')
                status['command_source'] = 'native-focus-lost'
            if (native_chat_mode and not opening_chat and
                    (not browser.proc or browser.proc.poll() is not None)):
                await set_mode(False)
            if native_chat_mode and not opening_chat and not bridge.get('native_alice_paused'):
                if voice_missing_since is None:
                    voice_missing_since = time.monotonic()
                elif time.monotonic() - voice_missing_since >= 3 and not serial.locked():
                    await execute('close')
                    status['command_source'] = 'web-call-ended'
                    status['auto_closed_calls'] = status.get('auto_closed_calls', 0) + 1
                    save()
            else:
                voice_missing_since = None
            if not serial.locked():
                async with serial:
                    with contextlib.suppress(Exception):
                        await browser.remember_current()
            save()
            await asyncio.sleep(.2)
    finally:
        music_watcher.cancel()
        if spoken_action:
            spoken_action.cancel()
            await asyncio.gather(spoken_action, return_exceptions=True)
        server.close()
        await server.wait_closed()
        SOCKET.unlink(missing_ok=True)
        await browser.close()
        with contextlib.suppress(Exception):
            if native_ready.is_set():
                await set_mode(False)
        native_watcher.cancel()
        await asyncio.gather(native_watcher, music_watcher, return_exceptions=True)
        status['phase'] = 'stopped'
        save()
        lock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['run', 'open', 'new', 'close', 'status', 'inspect'])
    args = parser.parse_args()
    asyncio.run(run() if args.action == 'run' else client(args.action))
