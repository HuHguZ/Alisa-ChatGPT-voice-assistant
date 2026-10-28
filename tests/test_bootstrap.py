"""The downloadable installer must validate a bundle before executing it."""
import hashlib,io,json,subprocess,tarfile,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

class Bootstrap(unittest.TestCase):
 def bundle(self,folder,extra=None,wrong_manifest=False):
  files={'scripts/release.py':b'print("BOOTSTRAP_FIXTURE_INSTALLED")\n'}
  hashes={name:hashlib.sha256(data).hexdigest() for name,data in files.items()}
  if wrong_manifest:hashes['scripts/release.py']='0'*64
  files['package-manifest.json']=json.dumps({'package':'alisa-voice','files':hashes}).encode()
  archive=folder/'bundle.tar.gz'
  with tarfile.open(archive,'w:gz') as output:
   for name,data in files.items():
    item=tarfile.TarInfo('alisa-voice-linux/'+name);item.size=len(data);output.addfile(item,io.BytesIO(data))
   if extra:
    item=tarfile.TarInfo(extra);item.size=1;output.addfile(item,io.BytesIO(b'x'))
  Path(str(archive)+'.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  bundle.tar.gz\n')
  return archive
 def run_bootstrap(self,archive):
  return subprocess.run(['sh',str(ROOT/'bootstrap-linux.sh'),'--archive',str(archive),'--no-run'],capture_output=True,text=True,timeout=15)
 def test_verified_bundle_runs_without_opening_cli(self):
  with tempfile.TemporaryDirectory() as folder:
   result=self.run_bootstrap(self.bundle(Path(folder)))
   self.assertEqual(result.returncode,0,result.stderr);self.assertIn('BOOTSTRAP_FIXTURE_INSTALLED',result.stdout)
 def test_corrupt_download_is_rejected_before_execution(self):
  with tempfile.TemporaryDirectory() as folder:
   archive=self.bundle(Path(folder));archive.write_bytes(archive.read_bytes()+b'corrupt')
   result=self.run_bootstrap(archive)
   self.assertNotEqual(result.returncode,0);self.assertIn('SHA-256',result.stderr);self.assertNotIn('BOOTSTRAP_FIXTURE_INSTALLED',result.stdout)
 def test_parent_path_is_rejected_before_extraction(self):
  with tempfile.TemporaryDirectory() as folder:
   result=self.run_bootstrap(self.bundle(Path(folder),'alisa-voice-linux/../../outside'))
   self.assertNotEqual(result.returncode,0);self.assertIn('путь в архиве',result.stderr);self.assertNotIn('BOOTSTRAP_FIXTURE_INSTALLED',result.stdout)
 def test_modified_file_is_rejected_even_with_valid_archive_checksum(self):
  with tempfile.TemporaryDirectory() as folder:
   result=self.run_bootstrap(self.bundle(Path(folder),wrong_manifest=True))
   self.assertNotEqual(result.returncode,0);self.assertIn('Повреждён файл',result.stderr);self.assertNotIn('BOOTSTRAP_FIXTURE_INSTALLED',result.stdout)
