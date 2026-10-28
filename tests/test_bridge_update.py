import subprocess,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
from alisa_cli import state,install,storage

class BridgeUpdate(unittest.TestCase):
 def test_failed_active_update_restores_files_and_restarts_previous_runtime(self):
  c=state.defaults('bridge');c.update(host='192.0.2.44',installed=True)
  path='/data/opt/alice-voice/profile.sh';files={path:b'working profile'};events=[]
  class Station:
   def run(self,command,**kwargs):
    if command.startswith('cat '):return subprocess.CompletedProcess([],0,stdout=files[command[4:]])
    raise AssertionError(command)
   def put(self,path,data,mode=0o600):events.append('write');files[path]=data
   def configure_bridge(self):return 'fake-host',None
  def staged(host,write_file):
   write_file(path,b'candidate',0o700);raise RuntimeError('connection dropped')
  loader=types.SimpleNamespace(exec_module=lambda module:None)
  module=types.SimpleNamespace(stage_station=staged)
  with tempfile.TemporaryDirectory() as folder,patch.object(install,'HOME',Path(folder)),patch.object(install,'require',return_value={}),patch.object(install,'Station',return_value=Station()),patch.object(storage,'report',return_value={'enough':True}),patch.object(storage,'payload',return_value={path:b'candidate'}),patch.object(install,'status',return_value={'phase':'running'}),patch.object(install,'stop',side_effect=lambda c:events.append('stop')),patch.object(install,'start',side_effect=lambda c:events.append('restart')),patch.object(install.importlib.util,'spec_from_file_location',return_value=types.SimpleNamespace(loader=loader)),patch.object(install.importlib.util,'module_from_spec',return_value=module):
   with self.assertRaisesRegex(RuntimeError,'connection dropped'):install.install(c)
  self.assertEqual(files[path],b'working profile')
  self.assertEqual(events,['stop','write','write','restart'])
  self.assertTrue(c['installed'])
