#!/usr/bin/env python3
"""Download pinned official sing-box archives, verify SHA-256, extract one file."""
import hashlib,io,tarfile,urllib.request
from pathlib import Path
VERSION='1.12.0'
HASHES={'linux-armv7':'ae30c7c3ef11a9351f3f2f40672ecf48ddc6393bd0cdb760ee8cb508f10a812d',
 'linux-amd64':'f59b1253ae0143997cb46915af30d14a33431dcbd7e39edacfcde8d73050faaf'}
ROOT=Path(__file__).resolve().parents[1]
def fetch(target):
 dest=ROOT/'assets/vpn'/target/'sing-box'
 if dest.exists():return dest
 url=f'https://github.com/SagerNet/sing-box/releases/download/v{VERSION}/sing-box-{VERSION}-{target}.tar.gz'
 with urllib.request.urlopen(url,timeout=40) as r:content=r.read(80*1024*1024)
 if hashlib.sha256(content).hexdigest()!=HASHES[target]:raise RuntimeError('sing-box archive checksum mismatch')
 with tarfile.open(fileobj=io.BytesIO(content)) as archive:
  item=next(x for x in archive if x.isfile() and x.name.endswith('/sing-box'))
  data=archive.extractfile(item).read()
 dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data);dest.chmod(0o700)
 return dest
if __name__=='__main__':
 for target in HASHES:print(fetch(target))
