"""Control only our dedicated Chromium through a private DevTools pipe."""
import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
import secrets
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from bridge import BROWSER_STATE, MIC_SOURCE, SPEAKER, STATE_DIR, ROOT, close_browser

CONVERSATION = STATE_DIR / 'conversation.json'


def conversation_url(value):
    """Remember only an ordinary conversation URL, never query/session data."""
    try:
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or parsed.netloc != 'chatgpt.com':
            return None
        parts = parsed.path.strip('/').split('/')
        if len(parts) != 2 or parts[0] != 'c':
            return None
        return f'https://chatgpt.com/c/{UUID(parts[1])}'
    except (ValueError, TypeError, AttributeError):
        return None


class Browser:
    def __init__(self):
        self.proc = None
        self.reader_task = None
        self.transport = None
        self.writer = None
        self.pending = {}
        self.sequence = 0
        self.session = None
        self.last_url = 'https://chatgpt.com/'
        self.bootstrap_previous = not CONVERSATION.exists()
        try:
            self.last_url = conversation_url(json.loads(CONVERSATION.read_text()).get('url')) or self.last_url
        except (OSError, ValueError):
            pass
        self.background = True
        self.display_proc = None
        self.display_name = None
        self.authority = None

    async def start_display(self):
        if self.display_proc and self.display_proc.poll() is None:
            return
        local_binary = ROOT / 'display-tools/usr/bin/Xvfb'
        binary = str(local_binary) if local_binary.exists() else shutil.which('Xvfb')
        if not binary or not shutil.which('xauth'):
            raise RuntimeError('Install Xvfb and xauth to run the background browser')
        number = next((n for n in range(91, 191) if
                       not Path(f'/tmp/.X{n}-lock').exists() and
                       not Path(f'/tmp/.X11-unix/X{n}').exists()), None)
        if number is None:
            raise RuntimeError('No free virtual display')
        self.display_name = f':{number}'
        self.authority = STATE_DIR / f'Xauthority-{os.getpid()}'
        self.authority.write_bytes(b'')
        self.authority.chmod(0o600)
        cookie = secrets.token_hex(16)
        result = subprocess.run(['xauth', '-f', str(self.authority)],
                                input=f'add {self.display_name} . {cookie}\n'.encode(),
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode:
            raise RuntimeError('Virtual display authorization failed')
        with (STATE_DIR / 'display.log').open('ab') as log:
            self.display_proc = subprocess.Popen(
                [str(binary), self.display_name, '-screen', '0', '1100x850x24',
                 '-nolisten', 'tcp', '-auth', str(self.authority), '-noreset'],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        for _ in range(30):
            if self.display_proc.poll() is not None:
                raise RuntimeError('Virtual display failed to start')
            if Path(f'/tmp/.X11-unix/X{number}').exists():
                return
            await asyncio.sleep(.1)
        raise RuntimeError('Virtual display startup timed out')

    async def launch(self):
        if self.proc and self.proc.poll() is None:
            return
        if self.proc:
            await self.close()
        close_browser()
        await self.start_display()
        profile = STATE_DIR / 'browser-profile'
        profile.mkdir(parents=True, mode=0o700, exist_ok=True)
        child_read, parent_write = os.pipe()
        parent_read, child_write = os.pipe()

        env = {**os.environ, 'PULSE_SOURCE': MIC_SOURCE, 'PULSE_SINK': SPEAKER}
        env.update(DISPLAY=self.display_name, XAUTHORITY=str(self.authority), XDG_SESSION_TYPE='x11')
        env.pop('WAYLAND_DISPLAY', None)
        # Remap descriptors in a fresh interpreter rather than preexec_fn in
        # this multi-threaded controller. No debugging TCP port is opened.
        wrapper = ('import os,sys; a=int(sys.argv[1]); b=int(sys.argv[2]); '
                   'os.dup2(a,3); os.dup2(b,4); '
                   '[os.close(fd) for fd in {a,b} if fd not in {3,4}]; '
                   'os.execv(sys.argv[3],sys.argv[3:])')
        with (STATE_DIR / 'browser.log').open('ab') as log:
            self.proc = subprocess.Popen(
                [sys.executable, '-c', wrapper, str(child_read), str(child_write),
                 shutil.which('chromium'), f'--user-data-dir={profile}',
                 '--remote-debugging-pipe', '--no-first-run', '--no-default-browser-check',
                 '--autoplay-policy=no-user-gesture-required', '--ozone-platform=x11',
                 '--window-size=1100,850', f'--app={self.last_url}'],
                env=env, pass_fds=(child_read, child_write),
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        os.close(child_read)
        os.close(child_write)
        self.writer = parent_write
        BROWSER_STATE.write_text(json.dumps({'pid': self.proc.pid, 'profile': str(profile),
                                             'background': self.background,
                                             'display': self.display_name}) + '\n')
        BROWSER_STATE.chmod(0o600)
        reader = asyncio.StreamReader(limit=8 * 1024 * 1024)
        self.transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(parent_read, 'rb', buffering=0))

        async def receive():
            try:
                while True:
                    item = json.loads((await reader.readuntil(b'\0'))[:-1])
                    future = self.pending.pop(item.get('id'), None)
                    if future and not future.done():
                        if 'error' in item:
                            future.set_exception(RuntimeError(item['error'].get('message', 'Browser command failed')))
                        else:
                            future.set_result(item.get('result', {}))
            except (EOFError, OSError, ValueError):
                for future in list(self.pending.values()):
                    if not future.done():
                        future.set_exception(RuntimeError('Dedicated browser closed'))
                self.pending.clear()
        self.reader_task = asyncio.create_task(receive())
        for _ in range(25):
            result = await self.command('Target.getTargets')
            page = next((x for x in result['targetInfos'] if x['type'] == 'page' and
                         x['url'].startswith('https://chatgpt.com')), None)
            if page:
                attached = await self.command('Target.attachToTarget',
                                              {'targetId': page['targetId'], 'flatten': True})
                self.session = attached['sessionId']
                await self.command('Browser.grantPermissions',
                                   {'origin': 'https://chatgpt.com', 'permissions': ['audioCapture']})
                return
            await asyncio.sleep(.4)
        raise RuntimeError('ChatGPT page did not open')

    async def command(self, method, params=None, page=False):
        self.sequence += 1
        key = self.sequence
        message = {'id': key, 'method': method, 'params': params or {}}
        if page:
            message['sessionId'] = self.session
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            os.write(self.writer, json.dumps(message).encode() + b'\0')
            return await asyncio.wait_for(future, 20)
        finally:
            self.pending.pop(key, None)

    async def evaluate(self, expression):
        result = await self.command('Runtime.evaluate', {'expression': expression,
                                    'returnByValue': True, 'awaitPromise': True}, page=True)
        if 'exceptionDetails' in result:
            raise RuntimeError('Browser UI evaluation failed')
        return result.get('result', {}).get('value')

    async def buttons(self):
        return await self.evaluate("""Array.from(document.querySelectorAll('button')).map(b=>({
            label:b.getAttribute('aria-label')||'',testid:b.getAttribute('data-testid')||'',
            text:(b.innerText||'').slice(0,80),visible:b.getBoundingClientRect().width>0
        })).filter(b=>/voice|голос|микроф|законч|заверш|end|call|новый чат|new chat/i.test(b.label+' '+b.testid+' '+b.text))""")

    async def health(self):
        value = await self.evaluate("""(()=>{const text=document.body?.innerText||'';return {
            ready:document.readyState,buttons:document.querySelectorAll('button').length,
            verification:/verify you are human|checking your browser|just a moment|cloudflare|проверяем ваш браузер/i.test(text),
            login:/log in|sign in|войти/i.test(text),bodyLength:text.length,currentUrl:location.href}})()""")
        current = conversation_url(value.pop('currentUrl', None))
        value['conversation_matches_saved'] = bool(current and current == conversation_url(self.last_url))
        return value

    async def click_voice(self):
        point = await self.evaluate("""(()=>{const buttons=Array.from(document.querySelectorAll('button'));
            const b=buttons.find(b=>{const s=[b.getAttribute('aria-label'),b.getAttribute('data-testid'),b.title].join(' ');
                return !b.disabled&&b.getBoundingClientRect().width>0&&/voice|голосов|start.*call/i.test(s)
                    && !/end|stop|close|mute|заверш|законч|выкл/i.test(s)});
            if(!b)return null;const r=b.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()""")
        if not point:
            return False
        await self.command('Input.dispatchMouseEvent', {'type': 'mousePressed', **point,
                           'button': 'left', 'clickCount': 1}, page=True)
        await self.command('Input.dispatchMouseEvent', {'type': 'mouseReleased', **point,
                           'button': 'left', 'clickCount': 1}, page=True)
        return True

    async def new_chat(self):
        await self.command('Page.navigate', {'url': 'https://chatgpt.com/'}, page=True)
        self.last_url = 'https://chatgpt.com/'

    def remember_conversation(self, url):
        clean = conversation_url(url)
        if not clean or clean == self.last_url and CONVERSATION.exists():
            return
        self.last_url = clean
        self.bootstrap_previous = False
        self.write_conversation()

    def write_conversation(self):
        CONVERSATION.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temp = CONVERSATION.with_suffix('.new')
        temp.write_text(json.dumps({'url': self.last_url}) + '\n')
        temp.chmod(0o600)
        temp.replace(CONVERSATION)

    def forget_conversation(self):
        self.last_url = 'https://chatgpt.com/'
        self.bootstrap_previous = False
        self.write_conversation()

    async def remember_current(self):
        if self.proc and self.proc.poll() is None and self.session:
            self.remember_conversation(await self.evaluate('location.href'))

    async def resume_previous(self):
        current = await self.evaluate('location.href')
        if conversation_url(self.last_url):
            if conversation_url(current) != self.last_url:
                await self.command('Page.navigate', {'url': self.last_url}, page=True)
            return
        # First migration only: recover a recent sidebar link without reading
        # message text, conversation titles, cookies, or browser session files.
        if self.bootstrap_previous:
            for _ in range(16):
                recent = await self.evaluate("""(()=>{const links=Array.from(document.querySelectorAll(
                    '#history a[href^="/c/"], nav a[href^="/c/"]'));
                    const a=links.find(a=>a.getBoundingClientRect().width>0);
                    return a?.href||null})()""")
                if conversation_url(recent):
                    self.remember_conversation(recent)
                    await self.command('Page.navigate', {'url': self.last_url}, page=True)
                    return
                await asyncio.sleep(.25)
            self.bootstrap_previous = False

    async def close(self):
        if self.proc is None:
            close_browser()
        if self.proc and self.proc.poll() is None:
            with contextlib.suppress(Exception):
                await self.remember_current()
                await self.command('Browser.close')
            try:
                await asyncio.to_thread(self.proc.wait, 3)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    await asyncio.to_thread(self.proc.wait, 2)
        if self.transport:
            self.transport.close()
        if self.writer is not None:
            os.close(self.writer)
        if self.reader_task:
            self.reader_task.cancel()
            await asyncio.gather(self.reader_task, return_exceptions=True)
        self.proc = self.session = self.writer = self.transport = None
        if self.display_proc and self.display_proc.poll() is None:
            self.display_proc.terminate()
            try:
                await asyncio.to_thread(self.display_proc.wait, 3)
            except subprocess.TimeoutExpired:
                self.display_proc.kill()
                await asyncio.to_thread(self.display_proc.wait)
        if self.authority:
            self.authority.unlink(missing_ok=True)
        self.display_proc = self.display_name = self.authority = None
