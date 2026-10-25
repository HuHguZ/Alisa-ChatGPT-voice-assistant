#!/usr/bin/env python3
"""Local spoken commands and private IPC for the dedicated ChatGPT window."""
import argparse
import asyncio
from collections import deque
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import time

from bridge import ROOT, STATE_DIR, REMOTE, MIC_SOURCE, alive, read_state, dedicated_browser_uses_microphone, prepare_browser_audio, ssh, ssh_args, rms
from browser_control import Browser
from native_control import cancel_frame, pause_frame, recognition_event, send_frame
from mode_cue import play_closed
from playback_guard import PlaybackGuard

SOCKET = STATE_DIR / 'voice.sock'
STATE = STATE_DIR / 'voice.json'
PHRASES = {
    'открой чат': 'open',
    'создай чат': 'new', 'создай новый чат': 'new',
    'закрой чат': 'close', 'выйди из чата': 'close', 'закончи разговор': 'close',
}


def intent(text):
    value = re.sub(r'[^а-яё\s]', ' ', text.lower()).replace('ё', 'е')
    value = ' '.join(value.split())
    if not value.startswith('алиса '):
        return None
    return PHRASES.get(value.removeprefix('алиса ').removesuffix(' пожалуйста').strip())


def recognized_intent(result):
    action = intent(result.get('text', ''))
    words = result.get('result', [])
    confidences = [w.get('conf', 0) for w in words]
    if (not action or len(words) < 3 or words[0].get('word') != 'алиса' or
            confidences[0] < .75 or min(confidences) < .45 or
            sum(confidences) / len(confidences) < .8):
        return None
    return action


def resolve_command(action, words, context_action=None, active=False):
    # "открой" and "закрой" differ by a short onset. Only resolve an
    # uncertain opening as closing when ChatGPT is already active and a
    # separate close/new-chat decoder confidently heard the closing command.
    verb = 1 if words and words[0].get('word') == 'алиса' else 0
    if (active and action == 'open' and context_action == 'close' and
            len(words) > verb and words[verb].get('conf', 0) < .9):
        return 'close'
    return action


class MarkedCommands:
    """Keep an explicit wake marker across a short pause after 'Alice'."""
    def __init__(self):
        self.until = 0.0
        self.marker = None

    def consume(self, result, now=None):
        now = time.monotonic() if now is None else now
        text = result.get('text', '').strip()
        words = result.get('result', [])
        if text == 'алиса' and words and words[0].get('conf', 0) >= .75:
            self.until, self.marker = now + 3, words[0]
            return None
        if text in PHRASES and self.marker and now <= self.until:
            result = {'text': 'алиса ' + text, 'result': [self.marker] + words}
        action = recognized_intent(result)
        if action:
            self.until, self.marker = 0, None
        return action


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
    from vosk import Model, KaldiRecognizer, SetLogLevel
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = (STATE_DIR / 'voice.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    SetLogLevel(-1)
    model = Model(str(ROOT / 'models/vosk-model-small-ru-0.22'))
    grammar = ['алиса ' + p for p in PHRASES] + ['[unk]']
    recognizer = KaldiRecognizer(model, 16000, json.dumps(grammar, ensure_ascii=False))
    recognizer.SetWords(True)
    commands = MarkedCommands()
    close_grammar = ['алиса ' + p for p, action in PHRASES.items() if action != 'open'] + ['[unk]']
    close_recognizer = KaldiRecognizer(model, 16000, json.dumps(close_grammar, ensure_ascii=False))
    close_recognizer.SetWords(True)
    close_commands = MarkedCommands()
    browser = Browser()
    serial = asyncio.Lock()
    stop = asyncio.Event()
    for sig in [signal.SIGTERM, signal.SIGINT]:
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    status = {'pid': os.getpid(), 'phase': 'running', 'last_action': None,
              'successful_actions': 0, 'voice_commands': 0}
    quiet_until = 0.0
    native_proc = None
    opening_chat = False

    def save():
        temp = STATE.with_suffix('.new')
        temp.write_text(json.dumps(status, ensure_ascii=False, indent=2) + '\n')
        temp.chmod(0o600)
        temp.replace(STATE)

    async def send_native(frame):
        # Reuse the already connected local event stream: no SSH handshake is
        # needed at the moment Alice is about to answer the switching command.
        if native_proc and native_proc.returncode is None:
            try:
                native_proc.stdin.write(frame)
                await asyncio.wait_for(native_proc.stdin.drain(), .5)
                return
            except (OSError, TimeoutError):
                pass
        await asyncio.to_thread(send_frame, read_state()['host'], frame)

    async def cancel_native():
        await send_native(cancel_frame())
        status['native_dialogue_cancels'] = status.get('native_dialogue_cancels', 0) + 1

    async def pause_native():
        await send_native(pause_frame())
        status['native_music_pauses'] = status.get('native_music_pauses', 0) + 1

    async def start_voice(action):
        nonlocal opening_chat
        host = read_state()['host']
        await cancel_native()
        await pause_native()
        await asyncio.to_thread(ssh, host, f'touch {REMOTE}/native-muted')
        opening_chat = True
        try:
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
                await asyncio.to_thread(ssh, host, f'rm -f {REMOTE}/native-muted')
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
                if was_active:
                    await cancel_native()
                await asyncio.to_thread(ssh, read_state()['host'], f'rm -f {REMOTE}/native-muted')
                if was_active:
                    # The browser has stopped capturing and the native input
                    # is restored before we confirm the completed transition.
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
                return {'ok': True, 'buttons': await browser.buttons(),
                        'health': await browser.health()}
            elif action in ['open', 'new']:
                result = await start_voice(action)
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

    SOCKET.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(handle, path=str(SOCKET), limit=8192)
    SOCKET.chmod(0o600)
    capture = await asyncio.create_subprocess_exec(
        'pacat', '--record', '--raw', '--format=s16le', '--rate=16000', '--channels=1',
        f'--device={MIC_SOURCE}', '--latency-msec=80', '--client-name=AliceVoiceCommands',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    spoken_action = None
    last_command_at = 0.0

    def schedule(action, source):
        nonlocal spoken_action, last_command_at
        if ((spoken_action and not spoken_action.done()) or
                time.monotonic() - last_command_at <= 2):
            return
        last_command_at = time.monotonic()
        status['last_recognized_action'] = action
        status['command_source'] = source
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
        seen = deque(maxlen=64)
        while True:
            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    *ssh_args(read_state()['host']),
                    'exec socat STDIO TCP:127.0.0.1:9875,connect-timeout=2',
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL)
                native_proc = proc
                while True:
                    size = int.from_bytes(await proc.stdout.readexactly(4), 'little')
                    if not 0 < size <= 512 * 1024:
                        raise ValueError('Invalid native event length')
                    event = recognition_event(await proc.stdout.readexactly(size))
                    status['native_events_connected'] = True
                    if not event:
                        continue
                    request, text = event
                    if request in seen:
                        continue
                    seen.append(request)
                    # Native final recognition is already tied to a wake-word
                    # dialogue; its text normally excludes the wake marker.
                    action = intent(text) or intent('алиса ' + text)
                    if action and not read_state().get('native_alice_paused'):
                        await cancel_native()
                        schedule(action, 'native')
            except (OSError, ValueError, asyncio.IncompleteReadError):
                pass
            finally:
                native_proc = None
                status['native_events_connected'] = False
                if proc and proc.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        proc.terminate()
                    await proc.wait()
            await asyncio.sleep(2)

    async def listen():
        was_reply = False

        def receive_result(result, alternative=None):
            action = commands.consume(result)
            contextual = close_commands.consume(alternative or {})
            action = resolve_command(action, result.get('result', []), contextual,
                                     active=read_state().get('native_alice_paused', False))
            if result.get('text'):
                # Diagnostic labels only: keep unrelated recognized words and
                # their contents out of state and logs.
                known = {'алиса', '[unk]'} | {w for p in PHRASES for w in p.split()}
                status['decoder_result'] = {
                    'words': [w.get('word') if w.get('word') in known else '<other>'
                              for w in result.get('result', [])],
                    'confidences': [round(w.get('conf', 0), 3) for w in result.get('result', [])],
                    'action': action,
                    'closing_context': contextual,
                }
                save()
            if action:
                schedule(action, 'local')
                recognizer.Reset()
                close_recognizer.Reset()

        while True:
            block = await capture.stdout.readexactly(3200)
            status['microphone_rms'] = round(rms(block), 6)
            # Our own spoken reply must never issue commands. Continue draining
            # capture during UI actions so old audio cannot reopen a closed chat.
            try:
                light = json.loads((STATE_DIR / 'indicator.json').read_text())
                own_reply = bool(light.get('speaking') and
                                 time.time() - light.get('updated_at', 0) < 1)
            except (OSError, ValueError):
                own_reply = False
            if own_reply and not was_reply and not (spoken_action and not spoken_action.done()):
                # The browser can begin its answer before Vosk's endpoint.
                # Finalize the user's preceding utterance before suppressing
                # our outgoing speech, rather than losing a close command.
                receive_result(json.loads(recognizer.FinalResult()),
                               json.loads(close_recognizer.FinalResult()))
            was_reply = own_reply
            if own_reply or time.monotonic() < quiet_until or (spoken_action and not spoken_action.done()):
                status['decoder_gate'] = 'reply' if own_reply else 'transition'
                recognizer.Reset()
                close_recognizer.Reset()
                continue
            status['decoder_gate'] = 'listening'
            primary_final = recognizer.AcceptWaveform(block)
            closing_final = close_recognizer.AcceptWaveform(block)
            # A constrained partial can turn "включи музыку" into "включи чат"
            # until the utterance ends. Never cancel Alice on a partial. Exact
            # native final events still provide the fast switching path.
            if primary_final or closing_final:
                primary = recognizer.Result() if primary_final else recognizer.FinalResult()
                closing = close_recognizer.Result() if closing_final else close_recognizer.FinalResult()
                receive_result(json.loads(primary), json.loads(closing))
                recognizer.Reset()
                close_recognizer.Reset()

    listener = asyncio.create_task(listen())
    native_watcher = asyncio.create_task(watch_native())
    music_guard = PlaybackGuard(
        lambda: opening_chat or bool(browser.proc and browser.proc.poll() is None and
                                    read_state().get('native_alice_paused')), pause_native)
    music_watcher = asyncio.create_task(music_guard.run())
    save()
    try:
        while not stop.is_set():
            bridge = read_state()
            if not alive(bridge) or bridge.get('phase') != 'running':
                break
            if listener.done():
                listener.result()
            if native_watcher.done():
                native_watcher.result()
            if music_watcher.done():
                music_watcher.result()
            if music_guard.error:
                status['native_music_pause_error'] = music_guard.error
            else:
                status.pop('native_music_pause_error', None)
            if not serial.locked():
                async with serial:
                    with contextlib.suppress(Exception):
                        await browser.remember_current()
            save()
            await asyncio.sleep(.3)
    finally:
        listener.cancel()
        native_watcher.cancel()
        music_watcher.cancel()
        if spoken_action:
            spoken_action.cancel()
            await asyncio.gather(spoken_action, return_exceptions=True)
        server.close()
        await server.wait_closed()
        SOCKET.unlink(missing_ok=True)
        await browser.close()
        if capture.returncode is None:
            capture.terminate()
        await capture.wait()
        await asyncio.gather(listener, native_watcher, music_watcher, return_exceptions=True)
        status['phase'] = 'stopped'
        save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['run', 'open', 'new', 'close', 'status', 'inspect'])
    args = parser.parse_args()
    asyncio.run(run() if args.action == 'run' else client(args.action))
