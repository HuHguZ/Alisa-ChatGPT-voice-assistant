"""Fetch the pinned Debian ARMhf Opus library compatible with Plum glibc 2.30."""
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.request

URL = 'https://deb.debian.org/debian/pool/main/o/opus/libopus0_1.3.1-3_armhf.deb'
SHA256 = '9decf188fb6e02ac79d180a1dbab463a28c755a75d6471a0e32de31e0a9b4160'
LIB_SHA256 = '2e2042354cf00f21b4850b3166f5f9abef931956aafdac76661f8e64e3896c7d'


def fetch(output):
    destination = output / 'libopus.so.0'
    license_file = output / 'opus-copyright'
    if (destination.exists() and license_file.exists() and
            hashlib.sha256(destination.read_bytes()).hexdigest() == LIB_SHA256):
        return
    data = urllib.request.urlopen(URL, timeout=30).read()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError('Downloaded Opus package checksum mismatch')
    with tempfile.TemporaryDirectory(prefix='alice-opus-') as temp:
        package = Path(temp) / 'opus.deb'
        package.write_bytes(data)
        archive = subprocess.check_output(['ar', 'p', str(package), 'data.tar.xz'])
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:xz') as tar:
            for source, target in [('./usr/lib/arm-linux-gnueabihf/libopus.so.0.8.0', destination),
                                   ('./usr/share/doc/libopus0/copyright', license_file)]:
                member = tar.getmember(source)
                if not member.isfile():
                    raise RuntimeError('Unexpected Opus archive member')
                target.write_bytes(tar.extractfile(member).read())
    if hashlib.sha256(destination.read_bytes()).hexdigest() != LIB_SHA256:
        destination.unlink()
        raise RuntimeError('Extracted Opus library checksum mismatch')


if __name__ == '__main__':
    output = Path(__file__).resolve().parents[1] / 'build'
    output.mkdir(exist_ok=True)
    fetch(output)
