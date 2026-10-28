"""Build a private speech route from the pinned station configuration."""
import copy


def launcher_profile(stock, remote, microphone_mode):
    launch = copy.deepcopy(stock)
    counts = {'--platformd': 0, '--mediad': 0}
    for node in launch['nodes']:
        for task in node['tasks']:
            for kind in counts:
                if kind not in task.get('arg', []):
                    continue
                counts[kind] += 1
                env = task.get('envs', [])
                if any(value.startswith('LD_PRELOAD=') for value in env):
                    raise RuntimeError('A custom LD_PRELOAD is already configured')
                if kind == '--platformd':
                    helper = 'vqe_tap' if microphone_mode == 'vqe' else 'capture_tap'
                    extra = [f'LD_PRELOAD={remote}/{helper}.so',
                             f'ALICE_CAPTURE_SOCKET={remote}/microphone.sock',
                             f'ALICE_NATIVE_MUTE_FILE={remote}/native-muted']
                else:
                    extra = [f'LD_PRELOAD={remote}/tts_gate.so',
                             f'ALICE_QUASAR_CONFIG={remote}/quasar.speech.json',
                             f'ALSA_CONFIG_PATH={remote}/tts-asound.conf',
                             f'ALICE_TTS_GATE_FILE={remote}/tts.lease',
                             f'ALICE_TTS_GATE_STATUS={remote}/tts-gate.status']
                task['envs'] = env + extra
    if any(value != 1 for value in counts.values()):
        raise RuntimeError('Expected exactly one stock platformd and mediad task')
    return launch


def speech_profile(stock):
    config = copy.deepcopy(stock)
    params = config['audioclient']['controller']['playbackParams']
    common = params['common']['gstProperties']
    sink = ('audioconvert ! audioresample ! '
            'audio/x-raw,format=S16LE,rate=48000,channels=2 ! '
            'alsasink device=alice_native_tts')
    for kind in ('OPUS_STREAM', 'PCM_STREAM'):
        pipeline = params[kind]['gstPipeline']
        if pipeline == '{STREAM_PIPELINE}':
            pipeline = common['STREAM_PIPELINE']
        if 'appsrc name=stream-src' not in pipeline or pipeline.count('{SINK}') != 1:
            raise RuntimeError('Unverified native speech pipeline: ' + kind)
        params[kind]['gstPipeline'] = pipeline.replace('{SINK}', sink)
    return config


ALSA_CONFIG = '''</usr/share/alsa/alsa.conf>
pcm.alice_native_tts {
  type plug
  slave.pcm "default"
}
'''
