#!/usr/bin/env python3
"""One-time local bootstrap from an explicitly selected, idle Chromium profile.

No browser remains in the station runtime. Credentials are saved only to the
private state directory, never to stdout, logs or research evidence.
"""
import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import subprocess
import tempfile
import secrets


async def main(profile, destination, interactive=False, install=False, login_only=False, proxy=None,host=None,port=2222):
    if install and (not host or host.startswith('-') or any(c.isspace() for c in host)):
        raise ValueError('Для --install укажите --host; обычный установщик передаёт сессию сам')
    if not interactive and not (profile/'Default/Network/Cookies').exists() and not (profile/'Default/Cookies').exists():
        raise RuntimeError('Selected profile has no existing cookie database')
    # Avoid taking over a running user call or its profile lock, including
    # Chromium builds which flatten all argv into a single process title.
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
    from alisa_cli.login_browser import profile_pid
    if profile_pid(profile) is not None:raise RuntimeError('Selected dedicated browser profile is in use')
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args=(entry/'cmdline').read_bytes().split(b'\0')
        except OSError:
            continue
        if (f'--user-data-dir={profile}'.encode() in args and
                any(b'chromium' in x for x in args[:1])):
            raise RuntimeError('Selected dedicated browser profile is in use')
    chromium=shutil.which('chromium')
    if not chromium:
        raise RuntimeError('Chromium is needed only for this one-time import')
    screen=None;temp=None
    if interactive:
        if not os.environ.get('DISPLAY'):raise RuntimeError('Visible login requires the user desktop')
        profile.mkdir(parents=True,exist_ok=True,mode=0o700);profile.chmod(0o700)
        env={**os.environ,'XDG_SESSION_TYPE':'x11'}
    else:
        xvfb=shutil.which('Xvfb') or str(Path(__file__).resolve().parents[2]/'bridge/display-tools/usr/bin/Xvfb')
        if not Path(xvfb).exists():
            raise RuntimeError('Xvfb is needed only for the hidden import window')
        temp=tempfile.TemporaryDirectory(prefix='standalone-auth-')
        authority=Path(temp.name)/'Xauthority'
        authority.touch(mode=0o600)
        number=next(n for n in range(191,221) if not Path(f'/tmp/.X{n}-lock').exists())
        display=f':{number}'
        subprocess.run(['xauth','-f',str(authority)],input=f'add {display} . {secrets.token_hex(16)}\n'.encode(),
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
        screen=await asyncio.create_subprocess_exec(xvfb,display,'-screen','0','1100x850x24',
                 '-nolisten','tcp','-auth',str(authority),'-noreset',stdout=asyncio.subprocess.DEVNULL,
                 stderr=asyncio.subprocess.DEVNULL)
        for _ in range(40):
            if Path(f'/tmp/.X11-unix/X{number}').exists():break
            await asyncio.sleep(.1)
        env={**os.environ,'DISPLAY':display,'XAUTHORITY':str(authority),'XDG_SESSION_TYPE':'x11'}
    env.pop('WAYLAND_DISPLAY',None)
    child_read,parent_write=os.pipe()
    parent_read,child_write=os.pipe()
    wrapper=('import os,sys; a=int(sys.argv[1]); b=int(sys.argv[2]); '
             'os.dup2(a,3); os.dup2(b,4); '
             '[os.close(fd) for fd in {a,b} if fd not in {3,4}]; '
             'os.execv(sys.argv[3],sys.argv[3:])')
    proc=await asyncio.create_subprocess_exec(
        sys.executable,'-c',wrapper,str(child_read),str(child_write),chromium,
        f'--user-data-dir={profile}','--ozone-platform=x11','--remote-debugging-pipe',
        '--no-first-run','--no-default-browser-check',*(['--proxy-server='+proxy] if proxy else []),'https://chatgpt.com/',
        pass_fds=(child_read,child_write),stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,env=env)
    os.close(child_read);os.close(child_write)
    reader=asyncio.StreamReader(limit=4*1024*1024)
    transport,_=await asyncio.get_running_loop().connect_read_pipe(
        lambda:asyncio.StreamReaderProtocol(reader),os.fdopen(parent_read,'rb',buffering=0))
    counter=0
    async def command(method,params=None,session=None):
        nonlocal counter
        counter+=1
        request={'id':counter,'method':method,'params':params or {}}
        if session:request['sessionId']=session
        os.write(parent_write,json.dumps(request).encode()+b'\0')
        while True:
            msg=json.loads((await asyncio.wait_for(reader.readuntil(b'\0'),30))[:-1])
            if msg.get('id')==counter:
                if 'error' in msg:raise RuntimeError('Private browser command failed')
                return msg.get('result',{})
    try:
        session=None
        for _ in range(40):
            targets=await command('Target.getTargets')
            page=next((x for x in targets['targetInfos'] if x['type']=='page' and
                       (x['url'].startswith('https://chatgpt.com/') or
                        (interactive and x['url'].startswith('https://auth.openai.com/')))),None)
            if page:
                session=(await command('Target.attachToTarget',
                         {'targetId':page['targetId'],'flatten':True}))['sessionId']
                break
            await asyncio.sleep(.25)
        if not session:raise RuntimeError('Dedicated ChatGPT page did not open')
        data=None
        expression="location.origin==='https://chatgpt.com'?fetch('/api/auth/session',{credentials:'include'}).then(r=>r.json()).catch(()=>null):null"
        if login_only:expression="location.origin==='https://chatgpt.com'?fetch('/api/auth/session',{credentials:'include'}).then(r=>r.json()).then(s=>({authenticated:!!s.accessToken,page_ready:document.querySelectorAll('button').length>0&&!/just a moment|один момент|проверяем ваш браузер|verify you are human|checking your browser/i.test(document.title||'')&&!document.querySelector('#challenge-form, #challenge-running')})).catch(()=>null):null"
        for _ in range(900 if interactive else 20):
            if proc.returncode is not None:raise RuntimeError('Login window was closed before sign-in completed')
            try:
                result=await command('Runtime.evaluate',{
                    'expression':expression,
                    'awaitPromise':True,'returnByValue':True},session)
            except RuntimeError:
                # Login changes origins and destroys evaluation contexts. Wait
                # for the next context rather than closing the owner's window.
                if not interactive:raise
                await asyncio.sleep(1);continue
            data=result.get('result',{}).get('value')
            if isinstance(data,dict) and (data.get('accessToken') or (login_only and data.get('authenticated') and data.get('page_ready'))):break
            await asyncio.sleep(1)
        if login_only and isinstance(data,dict) and data.get('authenticated') and data.get('page_ready'):
            print('Browser login verified; no tokens exported.',flush=True);return
        if not data or not data.get('accessToken'):
            raise RuntimeError('Existing web login is not usable; owner sign-in is required')
        cookies=(await command('Network.getCookies',{'urls':['https://chatgpt.com/']},session)).get('cookies',[])
        allowed=[c for c in cookies if c['name'].startswith(
            ('__Secure-next-auth.session-token','__Secure-authjs.session-token')) or
            c['name'] in ('oai-did','cf_clearance')]
        if not any('session-token' in c['name'] for c in allowed):
            raise RuntimeError('No renewable web-session cookie was available')
        payload={'access_token':data['accessToken'],'expires':data.get('expires'),
                 'account_id':(data.get('account') or {}).get('id'),
                 'cookies':{c['name']:c['value'] for c in allowed},
                 'user_agent':(await command('Runtime.evaluate',{'expression':'navigator.userAgent',
                              'returnByValue':True},session)).get('result',{}).get('value')}
        destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as f:
            json.dump(payload,f);f.flush();os.fsync(f.fileno())
        destination.chmod(0o600)
        print('Private web session imported; credentials were not printed.')
        if install:
            subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=6','-p',str(port),'-l','root',host,
                'umask 077; cat > /data/opt/alisa-standalone/state/web-session.json.new && '
                'mv /data/opt/alisa-standalone/state/web-session.json.new /data/opt/alisa-standalone/state/web-session.json'],
                input=destination.read_bytes(),stdout=subprocess.DEVNULL,check=True,timeout=20)
            print('New login installed on the Station; no voice call was started.',flush=True)
    finally:
        with contextlib.suppress(Exception):
            await command('Browser.close')
        transport.close();os.close(parent_write)
        try:await asyncio.wait_for(proc.wait(),5)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):os.killpg(proc.pid,signal.SIGTERM)
            await proc.wait()
        if screen:screen.terminate();await screen.wait()
        if temp:temp.cleanup()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',required=True,type=Path)
    parser.add_argument('--out',type=Path,default=Path(__file__).resolve().parents[1]/'state/web-session.json')
    parser.add_argument('--interactive',action='store_true',help='Open one visible window for the owner to sign in')
    parser.add_argument('--install',action='store_true',help='Install the resulting login on the Station')
    parser.add_argument('--login-only',action='store_true')
    parser.add_argument('--proxy')
    parser.add_argument('--host',help='SSH endpoint used only with --install')
    parser.add_argument('--port',type=int,default=2222)
    args=parser.parse_args()
    asyncio.run(main(args.profile.resolve(),args.out.resolve(),args.interactive,args.install,args.login_only,args.proxy,args.host,args.port))
