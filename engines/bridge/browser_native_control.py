"""Ordinary Chromium controlled through its owner-installed local extension."""
import asyncio,contextlib,json,os,secrets,shutil,subprocess,tempfile
from pathlib import Path
from browser_control import Browser as PipeBrowser
from browser_extension import prepare
from bridge import BROWSER_STATE,MIC_SOURCE,SPEAKER,STATE_DIR,ROOT,close_browser

class Browser(PipeBrowser):
    async def launch(self):
        if self.proc and self.proc.poll() is None:return
        if self.proc:await self.close()
        close_browser();await self.start_display()
        profile=getattr(self,'profile',STATE_DIR/'browser-profile');profile.mkdir(parents=True,exist_ok=True,mode=0o700)
        extension=prepare(profile,ROOT)
        self.native_folder=tempfile.TemporaryDirectory(prefix='alisa-browser-')
        path=Path(self.native_folder.name)/'control.sock';token=secrets.token_hex(32)
        connected=asyncio.get_running_loop().create_future()
        self.native_stream=None
        async def accept(reader,writer):
            try:
                hello=json.loads(await asyncio.wait_for(reader.readline(),3))
                if not secrets.compare_digest(hello.get('hello',''),token) or connected.done():writer.close();return
                self.native_stream=writer
                self.writer=writer.get_extra_info('socket').dup().detach()
                self.transport=writer.transport
                async def receive():
                    try:
                        while True:
                            item=json.loads((await reader.readuntil(b'\0'))[:-1])
                            future=self.pending.pop(item.get('id'),None)
                            if future and not future.done():
                                if 'error' in item:future.set_exception(RuntimeError(item['error'].get('message','Browser command failed')))
                                else:future.set_result(item.get('result',{}))
                    except (EOFError,OSError,ValueError):
                        for future in list(self.pending.values()):
                            if not future.done():future.set_exception(RuntimeError('Dedicated browser closed'))
                        self.pending.clear()
                self.reader_task=asyncio.create_task(receive());connected.set_result(True)
            except (OSError,ValueError,asyncio.TimeoutError):writer.close()
        self.native_server=await asyncio.start_unix_server(accept,path=str(path),limit=2*1024*1024);path.chmod(0o600)
        env={**os.environ,'PULSE_SOURCE':MIC_SOURCE,'PULSE_SINK':SPEAKER,'DISPLAY':self.display_name,
             'XAUTHORITY':str(self.authority),'XDG_SESSION_TYPE':'x11','ALISA_BROWSER_SOCKET':str(path),'ALISA_BROWSER_TOKEN':token}
        env.pop('WAYLAND_DISPLAY',None)
        with (STATE_DIR/'browser.log').open('ab') as log:
            self.proc=subprocess.Popen([shutil.which('chromium'),'--user-data-dir='+str(profile),
                '--load-extension='+str(extension),'--ozone-platform=x11','--no-first-run','--no-default-browser-check',
                '--window-size=1100,850','--new-window',self.last_url],env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        BROWSER_STATE.write_text(json.dumps({'pid':self.proc.pid,'profile':str(profile),'background':True,'display':self.display_name,'transport':'native-messaging'})+'\n');BROWSER_STATE.chmod(0o600)
        try:await asyncio.wait_for(connected,15)
        except asyncio.TimeoutError:raise RuntimeError('Local browser extension did not connect') from None
        for _ in range(30):
            targets=await self.command('Target.getTargets')
            page=next((t for t in targets.get('targetInfos',[]) if t['url'].startswith('https://chatgpt.com')),None)
            if page:
                self.session=(await self.command('Target.attachToTarget',{'targetId':page['targetId']}))['sessionId'];return
            await asyncio.sleep(.3)
        raise RuntimeError('ChatGPT page did not open')

    async def command(self,method,params=None,page=False):
        if method=='Browser.close':
            if self.proc and self.proc.poll() is None:self.proc.terminate()
            return {}
        self.sequence+=1;key=self.sequence
        request={'id':key,'method':method,'params':params or {}}
        if page:request['sessionId']=self.session
        future=asyncio.get_running_loop().create_future();self.pending[key]=future
        try:
            os.write(self.writer,json.dumps(request).encode()+b'\0')
            return await asyncio.wait_for(future,40 if method=='Target.attachToTarget' else 20)
        finally:self.pending.pop(key,None)

    async def close(self):
        try:await super().close()
        finally:
            if getattr(self,'native_server',None):self.native_server.close();await self.native_server.wait_closed();self.native_server=None
            if getattr(self,'native_stream',None):self.native_stream.close();self.native_stream=None
            if getattr(self,'native_folder',None):self.native_folder.cleanup();self.native_folder=None
