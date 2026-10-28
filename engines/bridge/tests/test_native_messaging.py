"""Exercise actual Native Messaging framing without a browser or credentials."""
import json,os,socket,struct,subprocess,sys,tempfile,unittest
from pathlib import Path
from browser_extension import prepare
ROOT=Path(__file__).resolve().parents[1]

class NativeMessaging(unittest.TestCase):
 def test_private_manifest_allows_only_our_extension(self):
  with tempfile.TemporaryDirectory() as folder:
   profile=Path(folder)/'profile';extension=prepare(profile,ROOT)
   manifest=json.loads((extension/'manifest.json').read_text())
   host=json.loads((profile/'NativeMessagingHosts/local.alisa_voice.json').read_text())
   self.assertEqual(manifest['host_permissions'],['https://chatgpt.com/*'])
   self.assertNotIn('externally_connectable',manifest)
   self.assertEqual(len(host['allowed_origins']),1)
   self.assertRegex(host['allowed_origins'][0],r'^chrome-extension://[a-p]{32}/$')
   self.assertEqual(extension.stat().st_mode&0o777,0o700)
   self.assertEqual((profile/'NativeMessagingHosts/local.alisa_voice.json').stat().st_mode&0o777,0o600)
 def test_real_stdio_transport_preserves_frames_and_rejects_oversize(self):
  with tempfile.TemporaryDirectory() as folder:
   path=str(Path(folder)/'control.sock')
   with socket.socket(socket.AF_UNIX) as server:
    server.bind(path);server.listen();server.settimeout(5)
    process=subprocess.Popen([sys.executable,str(ROOT/'browser_native_host.py')],
     env={**os.environ,'ALISA_BROWSER_SOCKET':path,'ALISA_BROWSER_TOKEN':'fixture-token'},stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
     channel,_=server.accept()
     with channel:
      channel.settimeout(5);hello=b''
      while not hello.endswith(b'\n'):hello+=channel.recv(1)
      self.assertEqual(json.loads(hello),{'hello':'fixture-token'})
      raw=json.dumps({'id':7,'method':'Runtime.evaluate'}).encode()+b'\0';channel.sendall(raw[:3]);channel.sendall(raw[3:])
      length=struct.unpack('=I',process.stdout.read(4))[0]
      self.assertEqual(json.loads(process.stdout.read(length)),{'id':7,'method':'Runtime.evaluate'})
      raw=json.dumps({'id':7,'result':{'ok':True}}).encode()
      process.stdin.write(struct.pack('=I',len(raw))+raw);process.stdin.flush()
      answer=b''
      while not answer.endswith(b'\0'):answer+=channel.recv(4096)
      self.assertEqual(json.loads(answer[:-1]),{'id':7,'result':{'ok':True}})
      process.stdin.write(struct.pack('=I',1024*1024+1));process.stdin.flush()
      self.assertEqual(channel.recv(4096),b'')
      self.assertEqual(process.wait(timeout=5),0)
    finally:
     if process.poll() is None:process.kill();process.wait(timeout=5)
     for stream in (process.stdin,process.stdout,process.stderr):stream.close()
