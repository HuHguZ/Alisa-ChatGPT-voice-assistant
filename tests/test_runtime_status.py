import json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from alisa_cli import state,runtime

class BridgeStatus(unittest.TestCase):
 def test_dead_bridge_is_stopped_and_old_voice_error_is_not_current(self):
  c=state.defaults('bridge');c['host']='192.0.2.44'
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder)/'bridge/runtime/voice.json';p.parent.mkdir(parents=True);p.write_text(json.dumps({'pid':0,'error':'browser_verification_required'}))
   response=subprocess.CompletedProcess([],0,stdout=b'{"phase":"running","process_alive":false}',stderr=b'')
   with patch.object(runtime,'HOME',Path(folder)),patch.object(runtime,'bridge_command',return_value=response):result=runtime.status(c)
  self.assertEqual(result['phase'],'stopped');self.assertNotIn('error',result)
