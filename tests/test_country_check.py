import io,json,subprocess,unittest
from unittest.mock import patch
from alisa_cli import network,state,runtime

class CountryCheck(unittest.TestCase):
 def test_station_russian_ip_requires_vpn_and_no_chatgpt_or_voice_probe(self):
  c=state.defaults('native');c['host']='192.0.2.44'
  r=subprocess.CompletedProcess([],0,stdout=b'{"status":"success","countryCode":"RU","query":"198.51.100.10"}',stderr=b'')
  with patch.object(network,'Station') as station,patch.object(network.urllib.request,'urlopen') as pc:
   station.return_value.run.return_value=r;result=network.check(c)
  self.assertEqual(result,{'country':'RU','ip':'198.51.100.10','network_status':'Требуется VPN'})
  command=station.return_value.run.call_args.args[0]
  self.assertIn('wget',command);self.assertNotIn('chatgpt.com',command);pc.assert_not_called()

 def test_linux_checks_pc_address_and_other_country_passes(self):
  c=state.defaults('bridge')
  with patch.object(network,'Station') as station,patch.object(network.urllib.request,'urlopen',return_value=io.BytesIO(b'{"country_code":"DE","ip":"198.51.100.20"}')) as request:
   result=network.check(c)
  self.assertEqual(result,{'country':'DE','ip':'198.51.100.20','network_status':'Пройдена'})
  station.assert_not_called();self.assertNotIn('chatgpt.com',request.call_args.args[0].full_url)

 def test_required_vpn_off_never_checks_direct_station_exit(self):
  c=state.defaults('native');c['host']='192.0.2.44';c['vpn']['enabled']=True
  with patch.object(network,'Station') as station:
   station.return_value.run.return_value=subprocess.CompletedProcess([],1,stdout=b'',stderr=b'')
   result=network.check(c)
  self.assertIsNone(result['country']);self.assertEqual(station.return_value.run.call_count,1)
  self.assertIn('VLESS выключен',result['message'])

 def test_missing_or_invalid_country_is_unknown_not_passed(self):
  for value in [{'country_code':'DE','ip':'invalid'},{'country_code':'','ip':'198.51.100.1'},{'status':'fail','countryCode':'RU','query':'198.51.100.1'}]:
   self.assertIsNone(network.location(value))
  self.assertEqual(network.report()['network_status'],'Не удалось определить страну IP')

 def test_native_voice_argument_includes_state_before_voice(self):
  c=state.defaults('native');c['host']='192.0.2.44'
  with patch.object(runtime,'Station') as station:
   station.return_value.run.return_value=subprocess.CompletedProcess([],0,stdout=b'{"ok":true}',stderr=b'')
   runtime.native_action(c,'voice','cove')
  self.assertTrue(station.return_value.run.call_args.args[0].endswith('voice /data/opt/alisa-standalone/state cove'))

 def test_only_vpn_runtime_binds_webrtc_to_its_tun_address(self):
  for enabled in [False,True]:
   c=state.defaults('native');c['host']='192.0.2.44';c['vpn']['enabled']=enabled
   with patch.object(runtime,'Station') as station:
    runtime.apply_settings(c)
   env=next(call.args[1] for call in station.return_value.put.call_args_list if call.args[0].endswith('runtime.env'))
   self.assertEqual(b'ALISA_ICE_BIND_ADDRESS=172.31.254.1' in env,enabled)
