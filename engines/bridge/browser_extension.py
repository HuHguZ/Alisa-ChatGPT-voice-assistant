"""Generate the owner-only extension and its standard Native Messaging host."""
import base64,hashlib,json,os,shlex,sys
from pathlib import Path

PUBLIC_KEY='MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAuQhKniyaT0bbJ+oZeF0XqlUyzO6g0toG7ezYOmCJfwwST36cHqx18/90DMsCO3A7kYHNQyJyamlQBSqO0uRhh1o5S/IccQPSILppl9RFoXxl1hC2D0zmYqfFCSBENFs3a6c885dF7k8u93uIe9j8el4Non0BdOoHPHyc3MRMwbpSbNc1l/YTN6sAmh1euTfSexdbnkLcbfFHMA8+HbeT3I7TMLcssbvzia0hr/myLVFk3UAbmTaLOyytGQdhSmBYQ7EeyDPhSlpo3v0ceylEiX8uUe+Fni5wyTwyVNzJit1XxxrMQU6i1NER/prxtqgfmKohtFpf98a2xKcgK07edQIDAQAB'

def prepare(profile,root):
    profile=Path(profile);folder=profile/'alisa-browser-control'
    folder.mkdir(parents=True,exist_ok=True,mode=0o700);folder.chmod(0o700)
    public=base64.b64decode(PUBLIC_KEY)
    extension_id=''.join(chr(ord('a')+int(n,16)) for n in hashlib.sha256(public).hexdigest()[:32])
    manifest={'manifest_version':3,'name':'Alisa Voice local control','version':'1.0.0','key':PUBLIC_KEY,
        'permissions':['nativeMessaging','tabs','debugger','scripting','contentSettings'],
        'host_permissions':['https://chatgpt.com/*'],'background':{'service_worker':'worker.js'}}
    (folder/'manifest.json').write_text(json.dumps(manifest))
    (folder/'worker.js').write_bytes((Path(root)/'browser_worker.js').read_bytes())
    launcher=folder/'native-host'
    launcher.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(Path(root)/'browser_native_host.py'))+' "$@"\n')
    launcher.chmod(0o700)
    host={'name':'local.alisa_voice','description':'Alisa Voice private local browser transport',
          'path':str(launcher),'type':'stdio','allowed_origins':['chrome-extension://'+extension_id+'/']}
    hosts=profile/'NativeMessagingHosts';hosts.mkdir(exist_ok=True,mode=0o700)
    destination=hosts/'local.alisa_voice.json';destination.write_text(json.dumps(host));destination.chmod(0o600)
    return folder
