#!/usr/bin/env python3
"""Private Chromium Native Messaging transport; no web or TCP listener."""
import json,os,socket,struct,sys,threading

def main():
    path=os.environ.get('ALISA_BROWSER_SOCKET');token=os.environ.get('ALISA_BROWSER_TOKEN')
    if not path or not token:return 1
    channel=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);channel.connect(path)
    channel.sendall((json.dumps({'hello':token})+'\n').encode())
    def incoming():
        try:
            while True:
                header=sys.stdin.buffer.read(4)
                if len(header)!=4:break
                length=struct.unpack('=I',header)[0]
                if not 0<length<=1024*1024:break
                raw=sys.stdin.buffer.read(length)
                if len(raw)!=length:break
                value=json.loads(raw);channel.sendall(json.dumps(value).encode()+b'\0')
        except (OSError,ValueError):pass
        finally:
            try:channel.shutdown(socket.SHUT_RDWR)
            except OSError:pass
    thread=threading.Thread(target=incoming,daemon=True);thread.start()
    try:
        buffer=b''
        while True:
            chunk=channel.recv(65536)
            if not chunk:break
            buffer+=chunk
            if len(buffer)>2*1024*1024:break
            while b'\0' in buffer:
                raw,buffer=buffer.split(b'\0',1)
                raw=json.dumps(json.loads(raw)).encode()
                if len(raw)>1024*1024:return 1
                sys.stdout.buffer.write(struct.pack('=I',len(raw))+raw);sys.stdout.buffer.flush()
    except (OSError,ValueError,BrokenPipeError):pass
    finally:channel.close()
    return 0

if __name__=='__main__':raise SystemExit(main())
