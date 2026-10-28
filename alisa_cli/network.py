"""Country of the actual egress: station for native, Linux PC for bridge."""
import ipaddress,json,re,shlex,subprocess,urllib.request
from .ssh import Station

GEO='https://ipapi.co/json/'
SOURCES=(GEO,'https://ipwho.is/')
STATION_GEO='http://ip-api.com/json/?fields=status,countryCode,query'

def classify(country,status=None):
    if country=='RU':return 'Требуется VPN'
    if country:return 'Пройдена'
    return 'Не удалось определить страну IP'

def location(data):
    country=data.get('country_code') or data.get('countryCode')
    ip=data.get('ip') or data.get('query')
    if data.get('success') is False or data.get('status')=='fail':return None
    if not isinstance(country,str) or not re.fullmatch('[A-Z]{2}',country.upper()):return None
    try:ip=str(ipaddress.ip_address(ip))
    except (ValueError,TypeError):return None
    return country.upper(),ip

def report(country=None,ip=None,message=None):
    value={'country':country,'ip':ip,'network_status':classify(country)}
    if message:value['message']=message
    return value

def check(config):
    if config['mode']=='native':
        station=Station(config)
        command='wget -q -T 8 -O - '+shlex.quote(STATION_GEO)
        if config['vpn'].get('enabled'):
            if station.run('/data/opt/alisa-cli/vpn.sh status',check=False).returncode:
                return report(message='VLESS выключен. Запустите его и повторите проверку IP.')
            # Use the same fail-closed marked routing as GPT. No account or
            # ChatGPT endpoint is involved in this country-only check.
            command='set -e; test -r /data/opt/alisa-cli/mark.so; export ALISA_VPN_REQUIRED=1 LD_PRELOAD=/data/opt/alisa-cli/mark.so; '+command
        try:
            response=station.run(command,check=False,timeout=15)
            found=location(json.loads(response.stdout)) if response.returncode==0 else None
            if found:return report(*found)
        except (RuntimeError,ValueError,OSError,subprocess.SubprocessError):pass
        return report(message='Не удалось получить внешний IP колонки. Повторите проверку.')
    for url in SOURCES:
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'AlisaVoice/0.1'}),timeout=8) as response:
                found=location(json.load(response))
            if found:return report(*found)
        except (OSError,ValueError):pass
    return report(message='Не удалось получить внешний IP Linux-ПК. Повторите проверку.')
