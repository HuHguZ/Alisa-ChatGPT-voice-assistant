"""A fixed synthetic question verifies the browser microphone and reply route."""
import array,json,math,os,selectors,subprocess,time
from pathlib import Path
from .state import ROOT

def probe_bridge():
    def pulse(kind):return json.loads(subprocess.check_output(['pactl','-f','json','list',kind]))
    microphone=next((s['index'] for s in pulse('sinks') if s.get('name')=='alice_station_capture'),None)
    if microphone is None:raise RuntimeError('Виртуальный микрофон моста недоступен. Сначала запустите мост.')
    muted=[];recorder=None;question=None;selector=selectors.DefaultSelector()
    try:
        # Temporarily isolate the fixed test from ambient microphone samples.
        # No global source or other application's stream is muted.
        for stream in pulse('sink-inputs'):
            pid=stream.get('properties',{}).get('application.process.id','')
            if not str(pid).isdigit():continue
            try:command=Path('/proc/'+str(pid)+'/cmdline').read_bytes()
            except OSError:continue
            if stream.get('sink')==microphone and b'pacat' in command and b'alice_station_capture' in command and not stream.get('mute'):
                subprocess.run(['pactl','set-sink-input-mute',str(stream['index']),'1'],capture_output=True,check=True)
                muted.append(stream['index'])
        if not muted:raise RuntimeError('Не удалось подготовить микрофон для теста. Перезапустите мост и повторите.')
        recorder=subprocess.Popen(['pacat','--record','--raw','--format=s16le','--rate=48000','--channels=2',
            '--device=alice_station_speaker.monitor','--latency-msec=20'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        selector.register(recorder.stdout,selectors.EVENT_READ)
        started=time.monotonic();audible=0;peak=0
        while time.monotonic()-started<30:
            elapsed=time.monotonic()-started
            if question is None and elapsed>=2:
                question=subprocess.Popen(['paplay','--device=alice_station_capture',str(ROOT/'assets/host/voice-check.wav')],
                    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if selector.select(.1):
                raw=os.read(recorder.stdout.fileno(),3840)
                if not raw:break
                samples=array.array('h',raw[:len(raw)//2*2])
                rms=math.sqrt(sum(x*x for x in samples)/max(1,len(samples)))/32768
                if elapsed>=6:
                    peak=max(peak,rms)
                    if rms>.005:audible+=1
                    if audible>25:
                        return {'real_reply_verified':True,'synthetic_microphone_input':True,'audible_blocks':audible,'peak_rms':round(peak,4)}
        raise RuntimeError('Мост открыл Voice, но голосовой ответ на тестовый вопрос не получен. Проверьте сеть и лимит аккаунта.')
    finally:
        selector.close()
        for process in [question,recorder]:
            if process is not None:
                if process.poll() is None:process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()
        for index in muted:subprocess.run(['pactl','set-sink-input-mute',str(index),'0'],capture_output=True)
