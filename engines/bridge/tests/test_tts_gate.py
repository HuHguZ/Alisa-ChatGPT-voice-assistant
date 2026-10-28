"""Real ALSA file output proves suppression, passthrough and stale-lease release."""
import array
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tts_profile import speech_profile, launcher_profile

PCM_CHILD = r'''
import ctypes as c, sys
alsa=c.CDLL('libasound.so.2',mode=c.RTLD_GLOBAL)
api=c.CDLL(None)
api.snd_pcm_open.argtypes=[c.POINTER(c.c_void_p),c.c_char_p,c.c_int,c.c_int]
api.snd_pcm_set_params.argtypes=[c.c_void_p,c.c_int,c.c_int,c.c_uint,c.c_uint,c.c_int,c.c_uint]
api.snd_pcm_writei.argtypes=[c.c_void_p,c.c_void_p,c.c_ulong]
api.snd_pcm_writei.restype=c.c_long
api.snd_pcm_drain.argtypes=[c.c_void_p]
api.snd_pcm_close.argtypes=[c.c_void_p]
pcm=c.c_void_p()
assert api.snd_pcm_open(c.byref(pcm),sys.argv[1].encode(),0,0)==0
assert api.snd_pcm_set_params(pcm,2,3,2,48000,1,40000)==0
raw=(c.c_short*1920)(*([1000,-1000]*960))
offset=0
while offset<960:
 n=api.snd_pcm_writei(pcm,c.byref(raw,offset*4),960-offset)
 assert n>0,n
 offset+=n
assert api.snd_pcm_drain(pcm)==0
api.snd_pcm_close(pcm)
'''

CONFIG_CHILD = r'''
import ctypes as c,sys
api=c.CDLL(None)
api.fopen64.argtypes=[c.c_char_p,c.c_char_p];api.fopen64.restype=c.c_void_p
api.fileno.argtypes=[c.c_void_p];api.fileno.restype=c.c_int
api.fclose.argtypes=[c.c_void_p]
p=api.fopen64(sys.argv[1].encode(),sys.argv[2].encode())
if not p:raise SystemExit(3)
import os
sys.stdout.buffer.write(os.read(api.fileno(p),4096))
api.fclose(p)
'''


class Gate(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.lease = self.root/'tts.lease'
        self.output = self.root/'output.raw'
        config = self.root/'alsa.conf'
        config.write_text('pcm.null { type null }\n' + '\n'.join(
            f'pcm.{name} {{ type file slave.pcm "null" file "{self.output}" format "raw" }}'
            for name in ('alice_native_tts', 'other')))
        self.env = {**os.environ, 'LD_PRELOAD': str(ROOT/'build/tts_gate.host.so'),
                    'ALSA_CONFIG_PATH': str(config), 'ALICE_TTS_GATE_FILE': str(self.lease)}

    def lease_at(self, timestamp=None):
        self.lease.write_text(f'TTS1 {int((time.monotonic() if timestamp is None else timestamp)*1000)}\n')

    def pcm(self, name='alice_native_tts'):
        result = subprocess.run([sys.executable, '-c', PCM_CHILD, name],
                                env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return array.array('h', self.output.read_bytes())

    def test_active_lease_silences_first_samples_without_affecting_other_pcm(self):
        self.lease_at()
        self.assertEqual(self.pcm().tolist(), [0]*1920)
        self.assertEqual(self.pcm('other').tolist(), [1000,-1000]*960)

    def test_missing_stale_future_and_malformed_leases_fail_open(self):
        self.assertEqual(self.pcm()[0], 1000)
        for content in (f'TTS1 {int((time.monotonic()-4)*1000)}\n',
                        f'TTS1 {int((time.monotonic()+60)*1000)}\n',
                        'TTS1 99999999999999999999999999\n', 'TTS1 x\n', '', 'TTS1 0'):
            with self.subTest(content=content):
                self.lease.write_text(content)
                self.assertEqual(self.pcm().tolist(), [1000,-1000]*960)

    def test_removing_a_lease_restores_unmodified_native_speech(self):
        self.lease_at()
        self.assertEqual(self.pcm()[0], 0)
        self.lease.unlink()
        self.assertEqual(self.pcm().tolist(), [1000,-1000]*960)

    def test_only_reading_the_stock_config_path_is_redirected(self):
        replacement = self.root/'quasar.json'
        replacement.write_text('{"test":"isolated"}')
        other = self.root/'other.json'
        other.write_text('{"test":"original"}')
        env = {**self.env, 'ALICE_QUASAR_CONFIG': str(replacement)}
        for path, content in (('/system/vendor/quasar/quasar.cfg', replacement.read_bytes()),
                              (str(other), other.read_bytes())):
            result = subprocess.run([sys.executable, '-c', CONFIG_CHILD, path, 'r'],
                                    env=env, capture_output=True)
            self.assertEqual((result.returncode, result.stdout), (0,content), result.stderr)
        result = subprocess.run([sys.executable, '-c', CONFIG_CHILD,
                                 '/system/vendor/quasar/quasar.cfg', 'r+'],
                                env=env, capture_output=True)
        self.assertEqual(result.returncode, 3)


class Profile(unittest.TestCase):
    def test_speech_hook_runs_in_mediad_and_capture_hook_stays_in_platformd(self):
        stock = {'nodes':[{'tasks':[
            {'arg':['--platformd'],'envs':['KEEP=1']},
            {'arg':['--mediad'],'envs':['KEEP=2']},
            {'arg':['--telemetry'],'envs':['KEEP=3']}]}]}
        result = launcher_profile(stock,'/data/opt/alice-voice','vqe')
        tasks = result['nodes'][0]['tasks']
        self.assertIn('LD_PRELOAD=/data/opt/alice-voice/vqe_tap.so',tasks[0]['envs'])
        self.assertIn('LD_PRELOAD=/data/opt/alice-voice/tts_gate.so',tasks[1]['envs'])
        self.assertFalse(any('tts_gate' in x or 'ALSA_CONFIG_PATH' in x for x in tasks[0]['envs']))
        self.assertFalse(any('vqe_tap' in x for x in tasks[1]['envs']))
        self.assertEqual(tasks[2],stock['nodes'][0]['tasks'][2])
        self.assertEqual(stock['nodes'][0]['tasks'][0]['envs'],['KEEP=1'])

    def test_incomplete_launchers_and_existing_preloads_are_rejected(self):
        for tasks in ([{'arg':['--platformd']}],
                      [{'arg':['--platformd']},{'arg':['--mediad'],'envs':['LD_PRELOAD=other.so']}],
                      [{'arg':['--platformd']},{'arg':['--mediad']},{'arg':['--mediad']}]):
            with self.subTest(tasks=tasks), self.assertRaises(RuntimeError):
                launcher_profile({'nodes':[{'tasks':tasks}]},'/tmp/bridge','raw')

    def test_only_speech_streams_change_and_original_config_is_preserved(self):
        params = {'common': {'gstProperties': {'STREAM_PIPELINE':
                  'appsrc name=stream-src format=time ! {input_media_type} ! {SINK}'}},
                  'OPUS_STREAM': {'gstPipeline': 'appsrc name=stream-src ! opusdec ! {SINK}'},
                  'PCM_STREAM': {'gstPipeline': '{STREAM_PIPELINE}'},
                  'AUDIO_FILE': {'gstPipeline':'{FILESRC} ! {SINK}'},
                  'TRACK': {'gstPipeline':'{MEDIA_PIPELINE}'},
                  'PCM_STREAM_WITH_BUFFER': {'gstPipeline':'{STREAM_PIPELINE}'}}
        stock = {'audioclient': {'controller': {'playbackParams': params}}, 'other': [1,2,3]}
        snapshot = copy.deepcopy(stock)
        result = speech_profile(stock)
        self.assertEqual(stock, snapshot)
        patched = result['audioclient']['controller']['playbackParams']
        for kind in ('OPUS_STREAM','PCM_STREAM'):
            self.assertIn('alsasink device=alice_native_tts', patched[kind]['gstPipeline'])
            self.assertIn('format=S16LE,rate=48000,channels=2', patched[kind]['gstPipeline'])
        for kind in ('AUDIO_FILE','TRACK','PCM_STREAM_WITH_BUFFER','common'):
            self.assertEqual(patched[kind], params[kind])
        self.assertEqual(result['other'], stock['other'])

    def test_unknown_speech_pipeline_is_rejected(self):
        stock = {'audioclient': {'controller': {'playbackParams': {
            'common': {'gstProperties':{}}, 'OPUS_STREAM': {'gstPipeline':'unverified'}}}}}
        with self.assertRaisesRegex(RuntimeError,'Unverified'):
            speech_profile(stock)
