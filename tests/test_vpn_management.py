"""VPN removal uses its file allowlist and rolls back interrupted deletion."""
import contextlib,copy,hashlib,json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from alisa_cli import vpn,state,runtime,autostart,cli,ui

def configured():
 c=state.defaults('native');c.update(host='192.0.2.44',installed=True,autostart=True)
 c['vpn'].update(enabled=True,required=True,engine='sing-box',autostart=True)
 return c

class VpnManagement(unittest.TestCase):
 def fixture(self,folder,fail=False):
  root=Path(folder);remote=root/'station';local=root/'computer';remote.mkdir();local.mkdir()
  for name in vpn.REMOTE_FILES:
   if name=='xray':continue
   (remote/name).write_bytes(b'vpn-fixture-'+name.encode())
  core=remote/'vpn.sh';core.write_text('#!/bin/sh\ncase "$1" in status) test -f "'+str(remote/'running')+'";; stop) rm -f "'+str(remote/'running')+'";; start) touch "'+str(remote/'running')+'";; esac\n');core.chmod(0o700)
  (remote/'running').touch();(remote/'boot.sh').write_bytes(b'shared-helper');(remote/'native-autostart').write_bytes(b'enabled')
  (remote/'mark.so').write_bytes(b'shared-marker');(remote/'unknown-user-file').write_bytes(b'keep')
  for name in vpn.LOCAL_FILES:
   path=local/'native'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'private-fixture')
  (local/'native/session.json').write_bytes(b'chatgpt-fixture')
  events=[];failure=[fail]
  class Station:
   def run(self,command,data=None,check=True,timeout=60):
    if command==runtime.NATIVE+'/run.sh start':
     events.append('start-client');return subprocess.CompletedProcess([],0,stdout=b'',stderr=b'')
    if failure[0] and command.startswith('test ! -L ') and command.endswith('/vless.json'):
     failure[0]=False;raise RuntimeError('simulated deletion interruption')
    command=command.replace(runtime.TOOLS,str(remote))
    result=subprocess.run(['sh','-c',command],input=data,capture_output=True)
    if check and result.returncode:raise RuntimeError('fixture command failed')
    return result
   def text(self,command,**kwargs):return self.run(command,**kwargs).stdout.decode()
   def put(self,path,data,mode=0o600):
    path=Path(path.replace(runtime.TOOLS,str(remote)))
    # All writes in this fixture must stay inside its temporary root.
    if str(path).startswith(runtime.NATIVE):return
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);path.chmod(mode)
  station=Station();stack=contextlib.ExitStack()
  stack.enter_context(patch.object(vpn,'HOME',local));stack.enter_context(patch.object(vpn,'Station',return_value=station))
  stack.enter_context(patch.object(vpn,'save',side_effect=lambda c:events.append('save')))
  stack.enter_context(patch.object(vpn,'status',return_value={'agent_connected':True}))
  stack.enter_context(patch.object(vpn,'stop',side_effect=lambda c:events.append('stop-client')))
  stack.enter_context(patch.object(vpn,'start',side_effect=lambda c:events.append('start-client')))
  stack.enter_context(patch.object(vpn,'apply_settings',side_effect=lambda c:events.append('settings')))
  stack.enter_context(patch.object(autostart,'remove_owned_boot'))
  stack.enter_context(patch.object(autostart,'ensure_boot'))
  return remote,local,events,stack

 def test_removal_deletes_active_vpn_files_and_keeps_account_shared_helpers(self):
  with tempfile.TemporaryDirectory() as folder:
   remote,local,events,stack=self.fixture(folder)
   with stack:
    c=configured();plan=vpn.removal_plan(c);result=vpn.remove(c,plan)
   self.assertTrue(result['vpn_removed']);self.assertEqual(c['vpn'],{'enabled':False,'required':False,'autostart':False})
   for name in vpn.REMOTE_FILES:self.assertFalse((remote/name).exists(),name)
   for name in vpn.LOCAL_FILES:self.assertFalse((local/'native'/name).exists(),name)
   for name in ['boot.sh','mark.so','native-autostart','unknown-user-file']:self.assertTrue((remote/name).exists(),name)
   self.assertEqual((local/'native/session.json').read_bytes(),b'chatgpt-fixture')
   self.assertTrue(c['autostart']);self.assertEqual(events[0],'stop-client');self.assertEqual(events[-1],'start-client')
   backup=Path(result['backup']);self.assertTrue((backup/'station/sing-box').is_file());self.assertEqual((backup/'computer/vless.json').stat().st_mode&0o777,0o600)

 def test_interrupted_deletion_restores_files_configuration_and_running_vpn(self):
  with tempfile.TemporaryDirectory() as folder:
   remote,local,events,stack=self.fixture(folder,fail=True)
   before={p.name:p.read_bytes() for p in remote.iterdir() if p.is_file()};c=configured();old=copy.deepcopy(c)
   with stack,self.assertRaisesRegex(RuntimeError,'simulated'):vpn.remove(c)
   self.assertEqual(c,old)
   for name,data in before.items():self.assertEqual((remote/name).read_bytes(),data,name)
   for name in vpn.LOCAL_FILES:self.assertTrue((local/'native'/name).exists())
   self.assertEqual(events[0],'stop-client');self.assertEqual(events[-1],'start-client')

 def test_modified_review_is_rejected_before_stopping_client(self):
  with tempfile.TemporaryDirectory() as folder:
   remote,local,events,stack=self.fixture(folder)
   with stack:
    c=configured();review=vpn.removal_plan(c);(remote/'sing-box').write_bytes(b'changed')
    with self.assertRaisesRegex(RuntimeError,'изменились'):vpn.remove(c,review)
   self.assertFalse(events)

 def test_remote_symlink_is_not_deleted_or_followed(self):
  with tempfile.TemporaryDirectory() as folder:
   remote,local,events,stack=self.fixture(folder)
   (remote/'sing-box').unlink();(remote/'sing-box').symlink_to(remote/'unknown-user-file')
   with stack,self.assertRaisesRegex(RuntimeError,'ссылкой'):vpn.removal_plan(configured())
   self.assertEqual((remote/'unknown-user-file').read_bytes(),b'keep')

 def test_configure_asks_autostart_before_installing_and_applies_choice(self):
  c=configured();c['vpn']['autostart']=False;events=[];selection={'selected':{'_engine':'xray'}}
  with patch.object(vpn,'select_server',return_value=selection),patch.object(cli,'yes',side_effect=lambda *a:events.append('prompt') or True),patch.object(vpn,'configure',side_effect=lambda *a,**k:events.append('install') or {'configured':True}),patch.object(autostart,'station_flag') as flag,patch.object(cli,'save'):
   result=cli.vpn_configure(c,'vless://fixture')
  self.assertEqual(events,['prompt','install']);self.assertTrue(result['autostart']);flag.assert_called_once_with(c,'vpn-autostart',True)

 def test_cancel_autostart_question_does_not_start_installing(self):
  with patch.object(vpn,'select_server',return_value={}),patch.object(cli,'yes',side_effect=ui.Back),patch.object(vpn,'configure') as install:
   with self.assertRaises(ui.Back):cli.vpn_configure(configured(),'vless://fixture')
  install.assert_not_called()

 def test_reentering_identical_server_does_not_stop_client_or_vpn(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);arm=root/'assets/vpn/linux-armv7/sing-box';arm.parent.mkdir(parents=True);arm.write_bytes(b'core')
   script=root/'station/vpn.sh';script.parent.mkdir();script.write_bytes(b'vpn script')
   digest=hashlib.sha256(b'core').hexdigest();c=configured();value={'outbounds':[]}
   def run(command,**kwargs):
    data=digest.encode()+b'  core\n' if command.startswith('sha256sum') else json.dumps(value).encode() if command=='cat '+runtime.TOOLS+'/vless.json' else b'sing-box\n' if command=='cat '+runtime.TOOLS+'/vpn-engine' else b''
    return subprocess.CompletedProcess([],0,stdout=data,stderr=b'')
   with patch.object(vpn,'ROOT',root),patch.object(vpn,'HOME',root/'private'),patch.object(vpn,'Station') as station,patch.object(vpn,'core_config',return_value=value),patch('alisa_cli.storage.report',return_value={'enough':True}),patch.object(vpn,'save'),patch.object(vpn,'status') as status,patch.object(vpn,'stop') as stop,patch.object(vpn,'start') as start,patch.object(vpn,'apply_settings') as apply:
    station.return_value.run.side_effect=run
    result=vpn.configure(c,'vless://fixture',selection={'selected':{},'servers':[{}],'rejected':0,'client':'standard'})
   stop.assert_not_called();start.assert_not_called();status.assert_not_called();apply.assert_not_called()
   self.assertIn('уже настроен',result['message']);self.assertTrue(c['vpn']['autostart'])
