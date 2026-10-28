#pragma once
#include <sonic.h>
#include <algorithm>
#include <cmath>
#include <stdexcept>

// Small streaming, pitch-preserving time-scale controller. The packet queue
// keeps compressed audio; Sonic only holds its short analysis/output window.
// Low reserve is recovered gradually during playback, before starvation.
class SpeechTempo {
    sonicStream stream=nullptr;
    float speed=1.0f,clock_speed=1.0f;
public:
    SpeechTempo(){clear();}
    SpeechTempo(const SpeechTempo &)=delete;
    SpeechTempo &operator=(const SpeechTempo &)=delete;
    ~SpeechTempo(){if(stream)sonicDestroyStream(stream);}
    void clear(){
        if(stream)sonicDestroyStream(stream);
        stream=sonicCreateStream(48000,2);speed=clock_speed=1.0f;
        if(!stream)throw std::runtime_error("tempo_allocation_failed");
        sonicSetQuality(stream,0);
    }
    float update(int reserve_ms){
        // A proportional term catches bursts; the slow clock integrator
        // compensates a source that generates speech below real time. Without
        // the latter a fixed low-water rule settles at a nearly empty buffer.
        int error=reserve_ms-400;
        clock_speed=std::clamp(clock_speed+std::clamp(error*.000002f,-.0005f,.0005f),.85f,1.0f);
        float wanted=std::clamp(clock_speed+error*.0004f,.82f,1.04f);
        speed+=std::clamp(wanted-speed,-.004f,.004f);
        if(wanted==1.0f&&std::abs(speed-1.0f)<.0041f)speed=1.0f;
        sonicSetSpeed(stream,speed);return speed;
    }
    int available()const{return sonicSamplesAvailable(stream);}
    void write(const short *samples,int frames){
        if(!sonicWriteShortToStream(stream,samples,frames))throw std::runtime_error("tempo_write_failed");
    }
    int read(short *samples,int frames){return sonicReadShortFromStream(stream,samples,frames);}
    void flush(){if(!sonicFlushStream(stream))throw std::runtime_error("tempo_flush_failed");}
};
