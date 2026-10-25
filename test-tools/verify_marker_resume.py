"""Exercise spoken switching using generated PCM; preserve the user's chat.

Run with the project's venv. Piper and its Russian voice are test tools only.
No microphone audio or conversation contents are read or saved by this test.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from piper import PiperVoice
from bridge import STATE_DIR, read_state

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
    original = CONVERSATION.read_bytes() if CONVERSATION.exists() else None
    report = {'raw_audio_saved': False,
              'test_input': 'Piper-generated speech into shared microphone source',
              'checks': []}
    voice = PiperVoice.load(str(ROOT / 'test-tools/piper/ru_RU-irina-medium.onnx'))

    def speak(text):
        chunks = list(voice.synthesize(text))
        audio = b''.join(c.audio_int16_bytes for c in chunks)
        pcm = subprocess.run(['ffmpeg', '-v', 'error', '-f', 's16le',
                              '-ar', str(chunks[0].sample_rate), '-ac', '1',
                              '-i', 'pipe:0', '-f', 's16le', '-ar', '48000', 'pipe:1'],
                             input=audio, stdout=subprocess.PIPE, check=True).stdout
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
        time.sleep(2.5)
        previous = read(VOICE).get('voice_commands', 0)
        speak(text)
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
            check['early_cancels'] = value.get('early_cancels', 0)
            check['passed'] = check['passed'] and check['early_cancels'] > 0
        report['checks'].append(check)
        print(json.dumps(check, ensure_ascii=False), flush=True)
        if not check['passed']:
            raise RuntimeError('Unexpected voice action')

    try:
        wait(ready)
        # All generated speech belongs to a separate disposable conversation.
        control('new')
        time.sleep(3)
        if not speak('Сколько будет два плюс два? Ответь кратко.'):
            wait(lambda: read(INDICATOR).get('speaking'), 40)
        report['reply_detected'] = True
        wait(lambda: not read(INDICATOR).get('speaking'), 40)
        command('Алиса, закрой чат.', 'close')
        wait(lambda: not read_state().get('native_alice_paused'), 10)
        report['alice_resumed_after_close'] = True
        test_conversation = CONVERSATION.read_bytes()
        report['test_conversation_saved'] = '/c/' in json.loads(test_conversation).get('url', '')
        command('Алиса, открой чат.', 'open')
        health = control('inspect')['health']
        report['resumed_saved_conversation'] = health['conversation_matches_saved']
        if not report['resumed_saved_conversation']:
            raise RuntimeError('Previous conversation was not resumed')
        command('Алиса, закрой чат.', 'close')
        report['native_events_connected'] = read(VOICE).get('native_events_connected', False)
        report['passed'] = all(c['passed'] for c in report['checks'])
    except Exception as exc:
        report['passed'] = False
        report['error'] = str(exc)
        report['command_diagnostic'] = read(VOICE).get('decoder_result')
    finally:
        try:
            control('close')
        except Exception:
            pass
        subprocess.run(['systemctl', '--user', 'stop', 'alice-voice-bridge.service'],
                       check=True, timeout=60)
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
