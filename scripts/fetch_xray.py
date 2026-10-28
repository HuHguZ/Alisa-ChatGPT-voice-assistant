#!/usr/bin/env python3
"""Fetch pinned official Xray release, verify archive, extract binary/license."""
import hashlib,io,urllib.request,zipfile
from pathlib import Path
VERSION='26.10.21'
TARGETS={'linux-armv7':('linux-arm32-v7a','89978508fa2ebb4a1e661f2c6ed51d241c4091b3cd17f4c65fdf20baae7dedac'),
         'linux-amd64':('linux-64','0eec3ed4163c273d8c8bf821f7d219e257921cf584aa3fb970715e458e631978')}
ROOT=Path(__file__).resolve().parents[1]
def fetch(target):
    name,digest=TARGETS[target];dest=ROOT/'assets/vpn'/target/'xray'
    if dest.exists():return dest
    url=f'https://github.com/XTLS/Xray-core/releases/download/v{VERSION}/Xray-{name}.zip'
    with urllib.request.urlopen(url,timeout=45) as response:data=response.read(80*1024*1024)
    if hashlib.sha256(data).hexdigest()!=digest:raise RuntimeError('Xray archive checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        binary=archive.read('xray')
        for entry in archive.namelist():
            if Path(entry).name.upper() in ('LICENSE','LICENSE.TXT'):
                license=ROOT/'assets/vpn/licenses/xray-LICENSE';license.parent.mkdir(exist_ok=True,parents=True);license.write_bytes(archive.read(entry))
    dest.parent.mkdir(exist_ok=True,parents=True);dest.write_bytes(binary);dest.chmod(0o700)
    return dest
if __name__=='__main__':
    for target in TARGETS:print(fetch(target))
