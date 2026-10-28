import hashlib,subprocess,tempfile,unittest
from pathlib import Path
from alisa_cli.ssh import Station

class AtomicUpload(unittest.TestCase):
 def transfer(self,path,payload,received):
  station=Station({});timeouts=[]
  def local(command,data=None,check=True,timeout=60):
   timeouts.append(timeout)
   result=subprocess.run(['sh','-c',command],input=received,capture_output=True)
   if check and result.returncode:raise RuntimeError('transfer rejected')
   return result
  station.run=local
  station.text=lambda command:hashlib.sha256(path.read_bytes()).hexdigest()
  station.put(str(path),payload)
  return timeouts

 def test_interrupted_input_preserves_existing_file(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'working binary';path.write_bytes(b'original')
   with self.assertRaises(RuntimeError):self.transfer(path,b'complete new binary',b'complete')
   self.assertEqual(path.read_bytes(),b'original')
   self.assertFalse(Path(str(path)+'.new').exists())

 def test_corrupt_input_of_same_size_preserves_existing_file(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'binary';path.write_bytes(b'original')
   with self.assertRaises(RuntimeError):self.transfer(path,b'correct',b'corrupt')
   self.assertEqual(path.read_bytes(),b'original')

 def test_complete_upload_replaces_file_with_extended_large_timeout(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'binary';data=b'x'*(2*1024*1024)
   timeouts=self.transfer(path,data,data)
   self.assertEqual(path.read_bytes(),data);self.assertGreater(timeouts[0],60)
