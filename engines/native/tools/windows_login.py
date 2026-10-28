#!/usr/bin/env python3
"""One-time ChatGPT sign-in for native mode from a Windows desktop.

Windows counterpart of import_web_session.py --interactive: a visible Chromium
window with its own profile, DevTools on an ephemeral loopback port (Windows
has no inherited --remote-debugging-pipe descriptors), and the same session
fields. The result goes through accounts.install_session, which verifies the
login on the Station before replacing the saved one. Tokens are never printed.
"""
import argparse,base64,json,os,secrets,socket,struct,subprocess,sys,time
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


def capture(chromium,profile,minutes):
    profile.mkdir(parents=True,exist_ok=True)
    port_file=profile/'DevToolsActivePort';port_file.unlink(missing_ok=True)
    proc=subprocess.Popen([str(chromium),f'--user-data-dir={profile}','--remote-debugging-port=0',
        '--remote-debugging-address=127.0.0.1','--no-first-run','--no-default-browser-check','--new-window',
        'https://chatgpt.com/'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if port_file.exists() and len(port_file.read_text().split())>=2:break
            time.sleep(.2)
        else:raise RuntimeError('Chromium did not open DevTools')
        port,path=port_file.read_text().split()[:2]
        tools=DevTools(int(port),path)
        expression=("location.origin==='https://chatgpt.com'?fetch('/api/auth/session',{credentials:'include'})"
                    ".then(r=>r.json()).catch(()=>null):null")
        deadline=time.time()+minutes*60;data=None;session=None
        print('Waiting for sign-in in the Chromium window...',flush=True)
        while time.time()<deadline:
            if proc.poll() is not None:raise RuntimeError('Login window was closed before sign-in completed')
            try:
                if session is None:
                    targets=tools.command('Target.getTargets')['targetInfos']
                    page=next((x for x in targets if x['type']=='page' and x['url'].startswith('https://chatgpt.com/')),None)
                    if page:session=tools.command('Target.attachToTarget',{'targetId':page['targetId'],'flatten':True})['sessionId']
                if session:
                    data=tools.command('Runtime.evaluate',{'expression':expression,'awaitPromise':True,'returnByValue':True},session).get('result',{}).get('value')
                    if isinstance(data,dict) and data.get('accessToken'):break
            except RuntimeError:
                # Sign-in navigates across origins and destroys the attached page.
                session=None
            time.sleep(1.5)
        if not isinstance(data,dict) or not data.get('accessToken'):raise RuntimeError('Sign-in was not completed in time')
        cookies=tools.command('Network.getCookies',{'urls':['https://chatgpt.com/']},session).get('cookies',[])
        allowed=[c for c in cookies if c['name'].startswith(('__Secure-next-auth.session-token','__Secure-authjs.session-token'))
                 or c['name'] in ('oai-did','cf_clearance')]
        if not any('session-token' in c['name'] for c in allowed):raise RuntimeError('No renewable web-session cookie was available')
        agent=tools.command('Runtime.evaluate',{'expression':'navigator.userAgent','returnByValue':True},session).get('result',{}).get('value')
        payload={'access_token':data['accessToken'],'expires':data.get('expires'),'account_id':(data.get('account') or {}).get('id'),
                 'cookies':{c['name']:c['value'] for c in allowed},'user_agent':agent}
        plan=(data.get('account') or {}).get('planType') or (data.get('user') or {}).get('plan_type')
        try:tools.command('Browser.close')
        except RuntimeError:pass
        return payload,plan
    finally:
        try:proc.wait(timeout=10)
        except subprocess.TimeoutExpired:proc.terminate()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minutes',type=int,default=20)
    parser.add_argument('--chromium',type=Path)
    args=parser.parse_args()
    chromium=args.chromium or next((p for p in CANDIDATES if p.exists()),None)
    if not chromium:raise RuntimeError('Chromium is not installed')
    config=state.load('native');folder=state.HOME/'native'
    payload,plan=capture(chromium,folder/'browser-profile',args.minutes)
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
