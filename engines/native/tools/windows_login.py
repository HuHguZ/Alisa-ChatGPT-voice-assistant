#!/usr/bin/env python3
"""One-time ChatGPT sign-in for native mode from a Windows desktop.

Windows counterpart of the Linux login: the owner signs in in a plain
Chromium window with its own profile (no DevTools attached, so site checks see
an ordinary browser). Then a short off-screen pass on that profile reads the
same session fields as import_web_session.py through DevTools on an ephemeral
loopback port (Windows has no inherited --remote-debugging-pipe descriptors). The result goes through accounts.install_session, which verifies the
login on the Station before replacing the saved one. Tokens are never printed.
"""
import argparse,base64,contextlib,json,os,secrets,socket,sqlite3,struct,subprocess,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from alisa_cli import state

CANDIDATES=[Path(os.environ.get('LOCALAPPDATA',''))/'Chromium/Application/chrome.exe',
            Path(os.environ.get('PROGRAMFILES',''))/'Chromium/Application/chrome.exe']


class DevTools:
    """Minimal RFC 6455 client: masked text frames out, CDP JSON in."""
    def __init__(self,port,path):
        self.sock=socket.create_connection(('127.0.0.1',port),timeout=30)
        key=base64.b64encode(secrets.token_bytes(16)).decode()
        self.sock.sendall((f'GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n'
                           f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        head=b''
        while b'\r\n\r\n' not in head:
            chunk=self.sock.recv(4096)
            if not chunk:raise RuntimeError('DevTools handshake failed')
            head+=chunk
        if b' 101 ' not in head.split(b'\r\n',1)[0]:raise RuntimeError('DevTools handshake refused')
        self.buffer=head.split(b'\r\n\r\n',1)[1];self.counter=0
    def exact(self,n):
        while len(self.buffer)<n:
            chunk=self.sock.recv(65536)
            if not chunk:raise RuntimeError('DevTools connection closed')
            self.buffer+=chunk
        data,self.buffer=self.buffer[:n],self.buffer[n:];return data
    def send_frame(self,opcode,payload):
        mask=secrets.token_bytes(4);n=len(payload)
        header=bytes([0x80|opcode])+(bytes([0x80|n]) if n<126 else
               bytes([0xFE])+struct.pack('>H',n) if n<65536 else bytes([0xFF])+struct.pack('>Q',n))
        self.sock.sendall(header+mask+bytes(b^mask[i%4] for i,b in enumerate(payload)))
    def receive(self):
        message=b''
        while True:
            first,second=self.exact(2)
            n=second&0x7F
            if n==126:n=struct.unpack('>H',self.exact(2))[0]
            elif n==127:n=struct.unpack('>Q',self.exact(8))[0]
            payload=self.exact(n);opcode=first&0x0F
            if opcode==9:self.send_frame(10,payload);continue
            if opcode==8:raise RuntimeError('DevTools connection closed')
            message+=payload
            if first&0x80:return json.loads(message)
    def command(self,method,params=None,session=None):
        self.counter+=1
        request={'id':self.counter,'method':method,'params':params or {}}
        if session:request['sessionId']=session
        self.send_frame(1,json.dumps(request).encode())
        while True:
            msg=self.receive()
            if msg.get('id')==self.counter:
                if 'error' in msg:raise RuntimeError('Private browser command failed')
                return msg.get('result',{})


def session_cookie_saved(profile):
    """Only cookie names are read; values stay encrypted in the profile."""
    for path in (profile/'Default/Network/Cookies',profile/'Default/Cookies'):
        if not path.exists():continue
        try:
            uri=path.resolve().as_uri()+'?mode=ro&immutable=1'
            with contextlib.closing(sqlite3.connect(uri,uri=True,timeout=1)) as db:
                if db.execute("select 1 from cookies where host_key like '%chatgpt.com' and "
                              "name like '%session-token%' limit 1").fetchone():return True
        except sqlite3.Error:pass
    return False


def sign_in(chromium,profile,minutes):
    """A plain window: no DevTools, no automation flags while the owner signs in
    and passes any Cloudflare check. It ends when the session cookie is on disk
    or the owner closes the window."""
    proc=subprocess.Popen([str(chromium),f'--user-data-dir={profile}','--no-first-run','--no-default-browser-check',
        '--new-window','https://chatgpt.com/'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    # An existing session would be detected at once, before Cloudflare has
    # cleared this visit; then the owner closes the window when the chat shows.
    already=session_cookie_saved(profile)
    print('Waiting for sign-in in a plain Chromium window'+(' (already signed in: close it when the chat opens)' if already else '')+'...',flush=True)
    deadline=time.time()+minutes*60
    while time.time()<deadline and proc.poll() is None:
        if not already and session_cookie_saved(profile):
            print('Session cookie saved; closing the sign-in window.',flush=True)
            time.sleep(5)
            # Without /F taskkill asks Chromium to close, so it flushes cookies.
            subprocess.run(['taskkill','/PID',str(proc.pid)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            break
        time.sleep(3)
    try:proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        subprocess.run(['taskkill','/T','/F','/PID',str(proc.pid)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        proc.wait()
    # The browser process tree may linger briefly and keep the profile locked.
    time.sleep(3)


def capture(chromium,profile,minutes,skip_sign_in=False):
    profile.mkdir(parents=True,exist_ok=True)
    if not skip_sign_in:sign_in(chromium,profile,minutes)
    port_file=profile/'DevToolsActivePort';port_file.unlink(missing_ok=True)
    # Second, short pass on the same profile, only to read its cookies. It opens
    # about:blank and uses browser-level DevTools, so no chatgpt.com page (and no
    # Cloudflare check) is loaded with a debugger attached. The Station then
    # obtains the access token itself through install_session's refresh.
    proc=subprocess.Popen([str(chromium),f'--user-data-dir={profile}','--remote-debugging-port=0',
        '--remote-debugging-address=127.0.0.1','--no-first-run','--no-default-browser-check','--new-window',
        '--window-position=-32000,-32000','--window-size=400,300','about:blank'],
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if port_file.exists() and len(port_file.read_text().split())>=2:break
            time.sleep(.2)
        else:raise RuntimeError('Chromium did not open DevTools')
        port,path=port_file.read_text().split()[:2]
        tools=DevTools(int(port),path)
        print('Reading the saved session cookies...',flush=True)
        cookies=[c for c in tools.command('Storage.getCookies').get('cookies',[])
                 if c.get('domain','').lstrip('.')=='chatgpt.com']
        allowed=[c for c in cookies if c['name'].startswith(('__Secure-next-auth.session-token','__Secure-authjs.session-token'))
                 or c['name'] in ('oai-did','cf_clearance')]
        if not any('session-token' in c['name'] for c in allowed):raise RuntimeError('Not signed in yet: no session cookie in the profile')
        agent=tools.command('Browser.getVersion').get('userAgent')
        print('reader: cookies',sorted(c['name'] for c in allowed),flush=True)
        payload={'cookies':{c['name']:c['value'] for c in allowed},'user_agent':agent}
        try:tools.command('Browser.close')
        except RuntimeError:pass
        return payload,None
    finally:
        try:proc.wait(timeout=10)
        except subprocess.TimeoutExpired:proc.terminate()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minutes',type=int,default=20)
    parser.add_argument('--chromium',type=Path)
    parser.add_argument('--skip-sign-in',action='store_true',help='Reuse the profile that is already signed in')
    args=parser.parse_args()
    chromium=args.chromium or next((p for p in CANDIDATES if p.exists()),None)
    if not chromium:raise RuntimeError('Chromium is not installed')
    config=state.load('native');folder=state.HOME/'native'
    payload,plan=capture(chromium,folder/'browser-profile',args.minutes,args.skip_sign_in)
    candidate=folder/('.login-candidate-'+str(time.time_ns())+'.json')
    state.write_private(candidate,json.dumps(payload).encode())
    try:
        from alisa_cli.accounts import install_session
        install_session(config,candidate,folder/'session.json')
    finally:candidate.unlink(missing_ok=True)
    print('Login verified on the Station and saved'+(f' (plan: {plan})' if plan else '')+'; tokens were not printed.',flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('LOGIN FAILED:',error,flush=True);sys.exit(1)
