"""Test return to Alice after the website ends a call; use generated speech."""
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bridge import STATE_DIR, read_state
from piper import PiperVoice


def control(action):
    result = subprocess.run([sys.executable, str(ROOT/'voice.py'), action],
                            check=True, capture_output=True, text=True, timeout=60)
    value = json.loads(result.stdout)
    if not value.get('ok', True):
        raise RuntimeError(value.get('error', 'Controller failed'))
    return value


def status():
    return json.loads((STATE_DIR/'voice.json').read_text())


def main():
    conversation = STATE_DIR/'conversation.json'
    original = conversation.read_bytes() if conversation.exists() else None
    report = {'raw_microphone_recorded': False, 'checks': []}
    try:
        until = time.monotonic()+75
        while time.monotonic() < until:
            try:
                ready = status()
                if (read_state().get('phase') == 'running' and ready.get('phase') == 'running'
                        and ready.get('native_events_connected')):
                    break
            except (OSError, ValueError):
                pass
            time.sleep(.2)
        else:
            raise TimeoutError('Bridge startup timed out')
        voice = PiperVoice.load(str(ROOT/'test-tools/piper/ru_RU-irina-medium.onnx'))
        chunks = list(voice.synthesize('Алиса, закрой чат.'))
        pcm = subprocess.run(['ffmpeg', '-v', 'error', '-f', 's16le',
                              '-ar', str(chunks[0].sample_rate), '-ac', '1', '-i', 'pipe:0',
                              '-f', 's16le', '-ar', '48000', 'pipe:1'],
                             input=b''.join(c.audio_int16_bytes for c in chunks),
                             stdout=subprocess.PIPE, check=True).stdout
        for _ in range(2):
            control('new')
            before = status()
            time.sleep(3)
            subprocess.run(['pacat', '--playback', '--raw', '--format=s16le', '--rate=48000',
                            '--channels=1', '--latency-msec=30', '--device=alice_station_capture',
                            '--client-name=AliceWebCloseVerification'],
                           input=bytes(19200)+pcm+bytes(96000), check=True, timeout=15)
            until = time.monotonic()+35
            while time.monotonic() < until:
                value = status()
                if (value.get('successful_actions', 0) > before.get('successful_actions', 0)
                        and value.get('last_action') == 'close'):
                    break
                time.sleep(.2)
            else:
                raise TimeoutError('Web voice command did not close the call')
            check = {'source': value.get('command_source'), 'action': value.get('last_action'),
                     'mode': value.get('native_agent', {}).get('mode'),
                     'close_cue': value.get('close_cues', 0) > before.get('close_cues', 0),
                     'native_commands_unchanged': value.get('native_agent', {}).get('commands') ==
                        before.get('native_agent', {}).get('commands')}
            check['passed'] = (check['source'] == 'web-call-ended' and check['action'] == 'close'
                               and check['close_cue'] and check['native_commands_unchanged'])
            report['checks'].append(check)
            print(json.dumps(check), flush=True)
            if not check['passed']:
                raise RuntimeError('Unexpected switching result')
        report['passed'] = True
    except Exception as exc:
        report['passed'] = False
        report['error'] = str(exc)
        report['controller'] = status()
        try:
            report['browser'] = control('inspect')
        except Exception:
            pass
    finally:
        try:
            control('close')
        except Exception:
            pass
        subprocess.run(['systemctl', '--user', 'stop', 'alice-voice-bridge.service'],
                       check=True, timeout=60)
        if original is None:
            conversation.unlink(missing_ok=True)
        else:
            conversation.write_bytes(original)
            conversation.chmod(0o600)
        subprocess.run(['systemctl', '--user', 'start', 'alice-voice-bridge.service'],
                       check=True, timeout=15)
        report['original_conversation_preserved'] = (
            conversation.read_bytes() == original if original is not None else not conversation.exists())
        path = ROOT/'web-close-test.json'
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        path.chmod(0o600)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report.get('passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
