"""Exercise spoken switching using generated PCM; preserve the user's chat.

Run with the project's venv. Piper and its Russian voice are test tools only.
No microphone audio or conversation contents are read or saved by this test.
"""
import json
import argparse
import contextlib
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from piper import PiperVoice
from bridge import MIC_SOURCE, SPEAKER, STATE_DIR, dedicated_browser_streams, read_state, ssh
from native_control import rpc_frame, send_frame

CONVERSATION = STATE_DIR / 'conversation.json'
VOICE = STATE_DIR / 'voice.json'
INDICATOR = STATE_DIR / 'indicator.json'
REPORT = ROOT / 'marker-resume-volume-test.json'


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def control(action):
    result = subprocess.run([sys.executable, str(ROOT / 'voice.py'), action],
                            capture_output=True, text=True, timeout=60, check=True)
    value = json.loads(result.stdout)
    if not value.get('ok', True):
        raise RuntimeError(value.get('error', 'Controller failed'))
    return value


def wait(predicate, timeout=45):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(.2)
    raise TimeoutError('Verification condition timed out')


def ready():
    bridge, voice = read_state(), read(VOICE)
    return (bridge.get('phase') == 'running' and voice.get('phase') == 'running'
            and Path('/proc/' + str(voice.get('pid', 'missing'))).exists())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--native-only', action='store_true')
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--activate', action='store_true',
                        help='Start native conversation via IPC; do not test the wake-word detector')
    args = parser.parse_args()
    original = CONVERSATION.read_bytes() if CONVERSATION.exists() else None
    capture_library = ROOT / 'build/capture_tap.so'
    original_capture = capture_library.read_bytes()
    report = {'raw_audio_saved': False,
              'wake_source': 'native conversation IPC' if args.activate else 'generated wake word',
              'test_input': 'Generated commands into native capture fixture; generated question into shared microphone source',
              'checks': []}
    voice = PiperVoice.load(str(ROOT / 'test-tools/piper/ru_RU-irina-medium.onnx'))

    def quiet():
        since = time.monotonic()
        until = since + 40
        while time.monotonic() < until:
            if read(INDICATOR).get('speaking'):
                since = time.monotonic()
            elif time.monotonic() - since > 2:
                return
            time.sleep(.1)
        raise TimeoutError('Voice output did not become quiet')

    def check_audio(stage):
        captures = dedicated_browser_streams('source-outputs')
        outputs = dedicated_browser_streams('sink-inputs')
        sources = json.loads(subprocess.check_output(['pactl', '-f', 'json', 'list', 'sources']))
        sinks = json.loads(subprocess.check_output(['pactl', '-f', 'json', 'list', 'sinks']))
        source = next(s for s in sources if s.get('name') == MIC_SOURCE)
        sink = next(s for s in sinks if s.get('name') == SPEAKER)
        check = {'stage': stage,
                 'capture_unmuted': bool(captures) and all(not e.get('mute') for e in captures),
                 'capture_routed_to_station': bool(captures) and all(
                     e.get('source') == source['index'] for e in captures),
                 'playback_unmuted': all(not e.get('mute') for e in outputs),
                 'playback_routed_to_station': all(e.get('sink') == sink['index'] for e in outputs)}
        report.setdefault('audio_checks', []).append(check)
        if not all(value for key, value in check.items() if key != 'stage'):
            raise RuntimeError('Dedicated browser audio is muted or routed incorrectly')

    def speak(text, native=False):
        # Keep the generated wake word identical across commands. Piper's
        # joined "Алиса, закрой чат" intonation is rejected by the native
        # spotter even when a time question from the same voice is accepted.
        parts = ['Алиса.', text[len('Алиса, '):]] if native and text.startswith('Алиса, ') else [text]
        chunks = [list(voice.synthesize(part)) for part in parts]
        rate = chunks[0][0].sample_rate
        audio = bytes(rate // 4 * 2).join(b''.join(c.audio_int16_bytes for c in part) for part in chunks)
        pcm = subprocess.run(['ffmpeg', '-v', 'error', '-f', 's16le',
                              '-ar', str(rate), '-ac', '1',
                              '-i', 'pipe:0', '-f', 's16le', '-ar', '48000', 'pipe:1'],
                             input=audio, stdout=subprocess.PIPE, check=True).stdout
        if native:
            if args.activate:
                send_frame(read_state()['host'], rpc_frame(b'AliceCapability', 8, b'\x08\x02\x38\x01'))
                time.sleep(.25)
            # Requires the temporary native_input.c fixture build, never a
            # microphone recording or a command injected into the user's chat.
            ssh(read_state()['host'],
                'umask 077; cat > /data/opt/alice-voice/synthetic-native-test.new; '
                'mv /data/opt/alice-voice/synthetic-native-test.new /data/opt/alice-voice/synthetic-native-test.pcm',
                data=bytes(19200) + pcm + bytes(48000))
            return False
        with ThreadPoolExecutor(max_workers=1) as executor:
            playback = executor.submit(subprocess.run,
                ['pacat', '--playback', '--raw', '--format=s16le',
                 '--rate=48000', '--channels=1', '--latency-msec=30',
                 '--device=alice_station_capture',
                 '--client-name=AliceCommandVerification'],
                input=bytes(19200) + pcm + bytes(48000), check=True, timeout=15)
            reply_seen = False
            while not playback.done():
                reply_seen |= bool(read(INDICATOR).get('speaking'))
                time.sleep(.05)
            playback.result()
        return reply_seen

    def command(text, expected):
        quiet()
        report['stage'] = 'command_' + expected
        previous = read(VOICE).get('voice_commands', 0)
        previous_cancels = read(VOICE).get('native_dialogue_cancels', 0)
        speak(text, native=True)
        value = wait(lambda: (s if s.get('voice_commands', 0) > previous else None)
                     if (s := read(VOICE)) else None, 40)
        check = {'phrase': text, 'expected': expected,
                 'actual': value.get('last_action'),
                 'passed': value.get('last_action') == expected}
        if expected == 'close':
            check['close_cues'] = value.get('close_cues', 0)
            check['cue_error'] = value.get('cue_error')
            check['passed'] = check['passed'] and check['close_cues'] > 0 and not check['cue_error']
        if expected == 'open':
            check['native_dialogue_cancels'] = value.get('native_dialogue_cancels', 0)
            check['passed'] = check['passed'] and check['native_dialogue_cancels'] > previous_cancels
        report['checks'].append(check)
        print(json.dumps(check, ensure_ascii=False), flush=True)
        if not check['passed']:
            raise RuntimeError('Unexpected voice action')

    def ordinary_request(chat):
        quiet()
        report['stage'] = 'ordinary_request_in_chat' if chat else 'ordinary_request_in_alice'
        before = read(VOICE).get('native_agent', {})
        speak('Алиса, сколько времени?', native=True)
        if chat:
            wait(lambda: read(VOICE).get('native_agent', {}).get('recognition_starts', 0) >
                 before.get('recognition_starts', 0), 15)
            wait(lambda: not read(VOICE).get('native_agent', {}).get('recognition_active', True), 15)
        else:
            wait(lambda: read(VOICE).get('native_agent', {}).get('native_speech_events', 0) >
                 before.get('native_speech_events', 0), 15)
            wait(lambda: read(VOICE).get('native_agent', {}).get('native_state') == 0, 15)
        after = read(VOICE).get('native_agent', {})
        check = {'chat_active': chat,
                 'no_chat_command_dispatched': after.get('commands') == before.get('commands'),
                 'native_speech_events_delta': after.get('native_speech_events', 0) - before.get('native_speech_events', 0),
                 'speech_stops_delta': after.get('speech_stops', 0) - before.get('speech_stops', 0)}
        check['passed'] = (check['no_chat_command_dispatched'] and check['speech_stops_delta'] == 0
                           and (check['native_speech_events_delta'] == 0 if chat
                                else check['native_speech_events_delta'] > 0))
        report.setdefault('ordinary_alice_checks', []).append(check)
        print(json.dumps(check), flush=True)
        if not check['passed']:
            raise RuntimeError('Native speech was not isolated by mode')

    try:
        fixture = ROOT / 'build/capture_tap.native-test.so'
        ssh(read_state().get('host', 'alice'),
            'rm -f /data/opt/alice-voice/synthetic-native-test.new /data/opt/alice-voice/synthetic-native-test.pcm')
        subprocess.run(['clang', '--target=arm-linux-gnueabihf', '-march=armv7-a',
                        '-mfpu=neon', '-mfloat-abi=hard', '-fuse-ld=lld', '-nostdlib',
                        '-Wl,--hash-style=both', '-O2', '-fPIC', '-shared',
                        str(ROOT / 'test-tools/native_input.c'), '-o', str(fixture)], check=True)
        shutil.copy2(fixture, capture_library)
        subprocess.run(['systemctl', '--user', 'restart', 'alice-voice-bridge.service'], check=True, timeout=60)
        wait(ready)
        if args.baseline:
            ordinary_request(chat=False)
            ordinary_request(chat=False)
            report['passed'] = True
        else:
            # All generated speech belongs to a separate disposable conversation.
            control('open' if args.native_only else 'new')
            report['stage'] = 'voice_reply'
            time.sleep(3)
            check_audio('new_chat')
            if not args.native_only:
                if not speak('Сколько будет два плюс два? Ответь кратко.'):
                    wait(lambda: read(INDICATOR).get('speaking'), 40)
                report['reply_detected'] = True
                wait(lambda: not read(INDICATOR).get('speaking'), 40)
            time.sleep(2)
            ordinary_request(chat=True)
            command('Алиса, закрой чат.', 'close')
            wait(lambda: not read_state().get('native_alice_paused'), 10)
            report['alice_resumed_after_close'] = True
            test_conversation = CONVERSATION.read_bytes()
            report['test_conversation_saved'] = '/c/' in json.loads(test_conversation).get('url', '')
            ordinary_request(chat=False)
            command('Алиса, открой чат.', 'open')
            health = control('inspect')['health']
            report['resumed_saved_conversation'] = health['conversation_matches_saved']
            if not report['resumed_saved_conversation']:
                raise RuntimeError('Previous conversation was not resumed')
            check_audio('reopened_chat')
            command('Алиса, закрой чат.', 'close')
            report['native_events_connected'] = read(VOICE).get('native_events_connected', False)
            report['passed'] = all(c['passed'] for c in report['checks'])
    except Exception as exc:
        report['passed'] = False
        report['error'] = str(exc)
        report['command_diagnostic'] = read(VOICE).get('decoder_result')
        report['failed_native_agent'] = read(VOICE).get('native_agent')
    finally:
        try:
            control('close')
        except Exception:
            pass
        subprocess.run(['systemctl', '--user', 'stop', 'alice-voice-bridge.service'],
                       check=True, timeout=60)
        capture_library.write_bytes(original_capture)
        with contextlib.suppress(Exception):
            ssh(read_state().get('host', 'alice'),
                'rm -f /data/opt/alice-voice/synthetic-native-test.new /data/opt/alice-voice/synthetic-native-test.pcm')
        if original is None:
            CONVERSATION.unlink(missing_ok=True)
        else:
            CONVERSATION.write_bytes(original)
            CONVERSATION.chmod(0o600)
        subprocess.run(['systemctl', '--user', 'start', 'alice-voice-bridge.service'],
                       check=True, timeout=15)
        wait(ready, 60)
        report['original_conversation_preserved'] = (
            CONVERSATION.read_bytes() == original if original is not None
            else not CONVERSATION.exists())
        report['final_controller'] = control('status')
        report['boost_db'] = read_state().get('playback_boost_db')
        report['native_alice_resumed'] = not read_state().get('native_alice_paused')
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        REPORT.chmod(0o600)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report.get('passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
