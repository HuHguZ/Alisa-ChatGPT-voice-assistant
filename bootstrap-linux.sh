#!/bin/sh
# Install the published Linux bundle; no compiler, pip or root is needed here.
set -eu
command -v python3 >/dev/null 2>&1 || {
    echo 'Нужен Python 3.10+: установите python3 (Debian/Fedora) или python (Arch) и повторите команду.' >&2
    exit 1
}
python3 - "$@" <<'PY'
import argparse,hashlib,json,os,platform,shutil,subprocess,sys,tarfile,tempfile,urllib.request
from pathlib import Path,PurePosixPath

def main():
    if sys.version_info < (3,10):raise RuntimeError('Нужен Python 3.10 или новее')
    if sys.platform!='linux' or platform.machine() not in ('x86_64','amd64'):
        raise RuntimeError('Этот пакет предназначен для Linux x86_64')
    parser=argparse.ArgumentParser(description='Установка Alisa Voice для Linux')
    parser.add_argument('--archive',type=Path,help='локальный архив с соседним файлом .sha256')
    parser.add_argument('--no-run',action='store_true',help='установить без открытия CLI')
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='alisa-install-') as temporary:
        temporary=Path(temporary)
        if args.archive:
            archive=args.archive.resolve();checksum=Path(str(archive)+'.sha256').read_text()
        else:
            base='https://github.com/rison1337/Alisa-ChatGPT-voice-assistant/releases/latest/download/'
            archive=temporary/'alisa-voice-linux.tar.gz'
            def download(name,destination,limit):
                request=urllib.request.Request(base+name,headers={'User-Agent':'Alisa-Voice-Installer'})
                with urllib.request.urlopen(request,timeout=60) as response,destination.open('wb') as output:
                    total=0
                    while True:
                        chunk=response.read(1024*1024)
                        if not chunk:break
                        total+=len(chunk)
                        if total>limit:raise RuntimeError('Размер загрузки превышает допустимый')
                        output.write(chunk)
            print('Загружаю Alisa Voice…',flush=True)
            download(archive.name,archive,180*1024*1024)
            check=temporary/'checksum';download(archive.name+'.sha256',check,4096);checksum=check.read_text()
        expected=checksum.split()[0]
        if len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected):raise RuntimeError('Некорректный SHA-256 релиза')
        digest=hashlib.sha256()
        with archive.open('rb') as source:
            for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
        if digest.hexdigest()!=expected:raise RuntimeError('SHA-256 архива не совпал; установка не началась')
        with tarfile.open(archive,'r:gz') as bundle:
            members=bundle.getmembers();total=0;names=set()
            for member in members:
                path=PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!='alisa-voice-linux':raise RuntimeError('Недопустимый путь в архиве')
                if not (member.isdir() or member.isfile()) or member.name in names:raise RuntimeError('Недопустимый файл в архиве')
                names.add(member.name);total+=member.size
            if total>512*1024*1024 or len(members)>10000:raise RuntimeError('Архив слишком большой')
            if shutil.disk_usage(temporary).free<total+16*1024*1024:raise RuntimeError('Недостаточно места для распаковки')
            # All paths, file types and sizes are checked before extraction.
            # Avoid Python-version-specific extractall defaults on 3.10+.
            for member in members:
                destination=temporary/member.name
                if member.isdir():destination.mkdir(parents=True,exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    with bundle.extractfile(member) as source,destination.open('wb') as output:shutil.copyfileobj(source,output)
                    destination.chmod(0o700 if member.mode&0o111 else 0o600)
        root=temporary/'alisa-voice-linux';manifest=json.loads((root/'package-manifest.json').read_text())
        if manifest.get('package')!='alisa-voice':raise RuntimeError('Неизвестный пакет')
        actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}-{'package-manifest.json'}
        if actual!=set(manifest['files']):raise RuntimeError('Состав пакета не совпал с manifest')
        for name,digest in manifest['files'].items():
            if hashlib.sha256((root/name).read_bytes()).hexdigest()!=digest:raise RuntimeError('Повреждён файл пакета')
        subprocess.run([sys.executable,str(root/'scripts/release.py'),'--install'],check=True)
try:main()
except (OSError,ValueError,KeyError,IndexError,RuntimeError,tarfile.TarError,subprocess.CalledProcessError) as error:
    print('Установка остановлена: '+str(error),file=sys.stderr);raise SystemExit(1)
PY
for alisa_arg in "$@"; do
    [ "$alisa_arg" != '--no-run' ] || exit 0
done
if ( : </dev/tty ) 2>/dev/null; then
    exec "$HOME/.local/bin/alisa" </dev/tty
fi
echo 'Запуск: ~/.local/bin/alisa'
