#!/usr/bin/env python3
"""Allowlisted local installation/archive; excludes user sessions and dev trees."""
import argparse,hashlib,json,os,shutil,tarfile,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BRIDGE=['bridge.py','voice.py','browser_control.py','indicator.py','audio_gain.py','audio_focus.py',
 'microphone_transport.py','packet_audio.py','tts_profile.py','mode_cue.py','playback_guard.py',
 'browser_native_control.py','browser_native_host.py','browser_extension.py','browser_worker.js','browser_login.py']

def files():
    selected=[ROOT/'alisa.py',ROOT/'install-linux.sh',ROOT/'bootstrap-linux.sh',ROOT/'README.md',ROOT/'THIRD-PARTY.md']
    selected+=list((ROOT/'alisa_cli').rglob('*.py'))
    selected+=list((ROOT/'alisa_cli/_vendor').rglob('*'))
    selected+=list((ROOT/'assets').rglob('*'))
    selected+=list((ROOT/'station').glob('*'))
    selected+=list((ROOT/'tests').glob('test_*.py'))
    selected+=list((ROOT/'engines/native/station').glob('*.c'))
    selected+=list((ROOT/'engines/native/station').glob('*.h'))
    selected+=list((ROOT/'docs').glob('*.md'))
    selected+=[ROOT/'docs'/name for name in ['pyyaml-source.json','terminal-ui-sources.json','native-dependencies.json','neteq-sources.json']]
    selected+=[ROOT/'scripts'/name for name in ['release.py','fetch_vpn.py','fetch_xray.py']]
    selected+=[ROOT/'engines/bridge'/name for name in BRIDGE]
    selected+=[ROOT/'engines/bridge/station'/name for name in ['profile.sh','indicator.sh']]
    selected+=[p for p in (ROOT/'engines/bridge/build').glob('*.so') if '.host.' not in p.name]
    selected+=[ROOT/'engines/bridge/build/libopus.so.0',ROOT/'engines/bridge/build/opus-copyright']
    selected+=[ROOT/'engines/native/tools'/name for name in ['deploy.py','import_web_session.py']]
    return sorted({p for p in selected if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts})

def populate(destination):
    manifest={}
    for source in files():
        relative=source.relative_to(ROOT);target=destination/relative
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        manifest[str(relative)]=hashlib.sha256(target.read_bytes()).hexdigest()
    (destination/'package-manifest.json').write_text(json.dumps({'schema':1,'package':'alisa-voice','files':manifest},indent=2)+'\n')

def install():
    destination=Path.home()/'.local/share/alisa-voice'
    launcher=Path.home()/'.local/bin/alisa'
    if launcher.exists() and str(destination/'alisa.py') not in launcher.read_text():raise RuntimeError('Команда alisa уже занята другим файлом; он сохранён. Запустите python3 alisa.py из папки проекта.')
    def owned(folder):
        try:return json.loads((folder/'package-manifest.json').read_text()).get('package')=='alisa-voice'
        except (OSError,ValueError):return False
    if destination.resolve()==ROOT.resolve():
        print('Установщик уже находится в',destination)
    else:
        destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.exists() and not owned(destination):raise RuntimeError('Каталог установки уже занят; существующие файлы сохранены.')
        with tempfile.TemporaryDirectory(prefix='.alisa-package-',dir=destination.parent) as temp:
            staged=Path(temp)/'runtime';staged.mkdir();populate(staged)
            backup=destination.with_name('alisa-voice.previous')
            if backup.exists():
                if not owned(backup):raise RuntimeError('Каталог резервной копии занят другим содержимым')
                shutil.rmtree(backup)
            if destination.exists():destination.rename(backup)
            try:staged.rename(destination)
            except BaseException:
                if backup.exists():backup.rename(destination)
                raise
    launcher.parent.mkdir(parents=True,exist_ok=True)
    import shlex
    launcher.write_text('#!/bin/sh\nexec python3 '+shlex.quote(str(destination/'alisa.py'))+' "$@"\n');launcher.chmod(0o755)
    print('Установлено. Запуск:',launcher)
    print('Настройки и аккаунты хранятся отдельно в ~/.local/state/alisa-voice.')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--install',action='store_true');args=p.parse_args()
    if args.install:install()
    else:
        out=ROOT/'dist';out.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'alisa-voice-linux';folder.mkdir();populate(folder)
            target=out/'alisa-voice-linux.tar.gz'
            with tarfile.open(target,'w:gz') as archive:archive.add(folder,arcname=folder.name)
        print(target)
        checksum=hashlib.sha256(target.read_bytes()).hexdigest()
        Path(str(target)+'.sha256').write_text(checksum+'  '+target.name+'\n')
