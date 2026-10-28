import shlex,subprocess,sys
from pathlib import Path
from .state import HOME,ROOT,write_private
from .ssh import Station
INIT='/etc/init.d/S99zz-alisa-voice'
BASE='/data/opt/alisa-cli'

def ensure_boot(station):
    station.put(BASE+'/boot.sh',(ROOT/'station/boot.sh').read_bytes(),0o700)
    body=(ROOT/'station/init.sh').read_bytes()
    # Restore the original mount mode even when writing the owned init entry fails.
    script='''set -eu
+was_ro=0
+case "$(awk '$2=="/" {print $4}' /proc/mounts)" in ro,*) was_ro=1;; esac
+if [ -e /etc/init.d/S99zz-alisa-voice ]; then grep -q ALISA_VOICE_INSTALLER /etc/init.d/S99zz-alisa-voice || exit 8; fi
+if [ "$was_ro" = 1 ]; then mount -o remount,rw /; fi
+trap 'if [ "$was_ro" = 1 ]; then mount -o remount,ro /; fi' EXIT
+cat > /etc/init.d/S99zz-alisa-voice.new
+chmod 755 /etc/init.d/S99zz-alisa-voice.new
+mv /etc/init.d/S99zz-alisa-voice.new /etc/init.d/S99zz-alisa-voice
+sync
+'''.replace('\n+','\n')
    station.run(script,data=body)

def station_flag(config,name,enabled):
    if name not in ('native-autostart','vpn-autostart'):raise ValueError('Неизвестный флаг автозапуска')
    if enabled and not config.get('installed'):raise RuntimeError('Сначала установите выбранный режим')
    station=Station(config)
    if enabled:ensure_boot(station);station.put(BASE+'/'+name,b'enabled\n')
    else:station.run('rm -f '+shlex.quote(BASE+'/'+name))

def unit_text(config):
    executable=shlex.quote(sys.executable);script=shlex.quote(str(ROOT/'alisa.py'))
    # systemd quoting differs from shell; absolute paths are escaped explicitly.
    def systemd_arg(p):return '"'+str(p).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
    return '\n'.join(['[Unit]','Description=Alisa browser voice bridge','After=pipewire-pulse.service','Wants=pipewire-pulse.service','',
        '[Service]','Type=simple','WorkingDirectory='+systemd_arg(ROOT),
        'Environment='+systemd_arg('ALISA_HOME='+str(HOME)),
        'ExecStart='+systemd_arg(sys.executable)+' '+systemd_arg(ROOT/'alisa.py')+' bridge run',
        'ExecStop='+systemd_arg(sys.executable)+' '+systemd_arg(ROOT/'alisa.py')+' bridge stop',
        'Restart=on-failure','RestartSec=15','TimeoutStopSec=45','KillMode=mixed','',
        '[Install]','WantedBy=default.target',''])

def set_enabled(config,enabled):
    if enabled and not config.get('installed'):raise RuntimeError('Сначала установите выбранный режим')
    if enabled:
        from .state import load,save
        other=load('bridge' if config['mode']=='native' else 'native')
        if other.get('autostart'):
            set_enabled(other,False);save(other)
    if config['mode']=='native':station_flag(config,'native-autostart',enabled)
    else:
        if sys.platform!='linux':raise RuntimeError('Автозапуск Windows будет проверен отдельным этапом')
        path=Path.home()/'.config/systemd/user/alisa-voice-bridge.service'
        write_private(path,unit_text(config).encode())
        subprocess.run(['systemctl','--user','daemon-reload'],check=True,capture_output=True)
        subprocess.run(['systemctl','--user','enable' if enabled else 'disable','alisa-voice-bridge.service'],check=True,capture_output=True)
    config['autostart']=enabled

def enabled(config):
    if config['mode']=='native':return Station(config).run('test -f '+BASE+'/native-autostart',check=False).returncode==0
    return subprocess.run(['systemctl','--user','is-enabled','alisa-voice-bridge.service'],capture_output=True).returncode==0

def remove_owned_boot(station):
    station.run('''set -eu
if [ ! -f /etc/init.d/S99zz-alisa-voice ]; then exit 0; fi
grep -q ALISA_VOICE_INSTALLER /etc/init.d/S99zz-alisa-voice || exit 0
if [ -f /data/opt/alisa-cli/native-autostart ] || [ -f /data/opt/alisa-cli/vpn-autostart ]; then exit 0; fi
was_ro=0
case "$(awk '$2=="/" {print $4}' /proc/mounts)" in ro,*) was_ro=1;; esac
if [ "$was_ro" = 1 ]; then mount -o remount,rw /; fi
trap 'if [ "$was_ro" = 1 ]; then mount -o remount,ro /; fi' EXIT
rm -f /etc/init.d/S99zz-alisa-voice
sync
''')
