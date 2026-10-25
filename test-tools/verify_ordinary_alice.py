"""Ensure ordinary Alice requests cannot cancel native speech or open a chat.

Run against an idle, running bridge with its venv and Piper test model.
Only generated PCM is injected; microphone recordings are never saved.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from piper import PiperVoice
from bridge import MIC_BACKEND, STATE_DIR, alive, read_state


def snapshot():
    state = read_state()
    voice = json.loads((STATE_DIR / 'voice.json').read_text())
    if not alive(state) or state.get('phase') != 'running' or voice.get('phase') != 'running':
        raise RuntimeError('Start the bridge before verification')
    if state.get('native_alice_paused'):
        raise RuntimeError('Close ChatGPT before verifying ordinary Alice requests')
    return {key: voice.get(key, 0) for key in [
        'native_dialogue_cancels', 'early_cancels', 'native_music_pauses',
        'successful_actions', 'voice_commands']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--first-only', action='store_true')
    args = parser.parse_args()
    report = {'raw_audio_saved': False, 'checks': []}
    baseline = snapshot()
    voice = PiperVoice.load(str(ROOT / 'test-tools/piper/ru_RU-irina-medium.onnx'))
    phrases = ['Алиса, включи музыку.', 'Алиса, продолжи музыку.',
               'Алиса, поставь песню.', 'Алиса, какая погода?',
               'Алиса, включи чат.', 'Алиса, запусти чат.']
    for phrase in phrases[:1] if args.first_only else phrases:
        chunks = list(voice.synthesize(phrase))
        pcm = subprocess.run([
            'ffmpeg', '-v', 'error', '-f', 's16le', '-ar', str(chunks[0].sample_rate),
            '-ac', '1', '-i', 'pipe:0', '-f', 's16le', '-ar', '48000', 'pipe:1'],
            input=b''.join(c.audio_int16_bytes for c in chunks),
            stdout=subprocess.PIPE, check=True).stdout
        subprocess.run([
            'pacat', '--playback', '--raw', '--format=s16le', '--rate=48000',
            '--channels=1', '--latency-msec=30', f'--device={MIC_BACKEND}',
            '--client-name=AliceOrdinaryRequestVerification'],
            input=bytes(19200) + pcm + bytes(288000), timeout=20, check=True)
        time.sleep(1)
        after = snapshot()
        check = {'phrase': phrase, 'passed': after == baseline,
                 'counter_changes': {key: after[key] - baseline[key] for key in baseline}}
        report['checks'].append(check)
        print(json.dumps(check, ensure_ascii=False), flush=True)
        if not check['passed']:
            break
    report['passed'] = all(check['passed'] for check in report['checks'])
    path = ROOT / 'ordinary-alice-command-test.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    path.chmod(0o600)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
