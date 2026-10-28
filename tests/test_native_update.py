"""An identical native bundle must not copy binaries or restart a live call."""
import hashlib,json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from alisa_cli import install,state,storage

class NativeUpdate(unittest.TestCase):
 def test_identical_bundle_is_skipped_with_existing_runtime_alive(self):
  c=state.defaults('native');c.update(host='192.0.2.44',installed=True)
  files={'/data/opt/alisa-standalone/bin/alisa-standalone':b'same-binary'}
  sizes={p:(len(data),hashlib.sha256(data).hexdigest()) for p,data in files.items()}
  with tempfile.TemporaryDirectory() as folder,patch.object(install,'HOME',Path(folder)),patch.object(install,'require',return_value={}),patch.object(install,'Station') as station,patch.object(install,'native_files',return_value=files),patch.object(storage,'report',return_value={'enough':True}),patch.object(storage,'remote_sizes',return_value=sizes),patch.object(install,'status',return_value={'agent_connected':True,'led_style':c['led']}),patch.object(install,'stop') as stop,patch.object(install,'start') as start,patch.object(install,'apply_settings') as settings,patch.object(install,'save'):
   station.return_value.text.return_value='{"audio_pipeline_revision":5}'
   result=install.install(c,activate=False)
  station.return_value.put.assert_not_called();station.return_value.run.assert_not_called();stop.assert_not_called();start.assert_not_called();settings.assert_called_once_with(c)
  self.assertEqual(result['files_updated'],0);self.assertEqual(result['files_unchanged'],1);self.assertTrue(result['started'])
  self.assertIn('уже актуальны',result['message'])
