import json,subprocess,unittest
from unittest.mock import patch
from alisa_cli import cli,state,voice_probe

class VoiceProbe(unittest.TestCase):
 def test_open_voice_ui_without_reply_is_failure_and_always_closes(self):
  response=subprocess.CompletedProcess([],0,stdout=b'{"ok":true,"voice_active":true}',stderr=b'')
  with patch('alisa_cli.runtime.bridge_env',return_value=({},'fake')),patch.object(cli.subprocess,'run',return_value=response) as run,patch.object(voice_probe,'probe_bridge',side_effect=RuntimeError('No reply')):
   with self.assertRaisesRegex(RuntimeError,'No reply'):cli.verify(state.defaults('bridge'))
  self.assertEqual([c.args[0][-1] for c in run.call_args_list],['open','close'])

 def test_microphone_stream_is_unmuted_if_recorder_fails_to_start(self):
  sinks=json.dumps([{'name':'alice_station_capture','index':10}]).encode()
  streams=json.dumps([{'sink':10,'index':20,'mute':False,'properties':{'application.process.id':'123'}}]).encode()
  with patch.object(voice_probe.subprocess,'check_output',side_effect=[sinks,streams]),patch.object(voice_probe.Path,'read_bytes',return_value=b'pacat\0--device=alice_station_capture\0'),patch.object(voice_probe.subprocess,'run') as run,patch.object(voice_probe.subprocess,'Popen',side_effect=OSError('Recorder failed')):
   with self.assertRaises(OSError):voice_probe.probe_bridge()
  self.assertEqual([c.args[0][-1] for c in run.call_args_list],['1','0'])
