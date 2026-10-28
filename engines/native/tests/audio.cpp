#include "playout.hpp"
#include "effects.hpp"
#include "tempo.hpp"
#include <cassert>
#include <iostream>
#include <vector>

std::vector<unsigned char> payload(int id){return {static_cast<unsigned char>(id)};}

int main(){
    // Regression: the former 160 ms ahead cutoff loses 9 out of these first
    // 15 consecutive frames despite zero network loss. All must be retained.
    RtpPlayout burst;
    for(int i=0;i<15;++i)burst.push(10000+i*960,1,payload(i),960,0);
    assert(!burst.pop(200000));
    for(int i=0;i<15;++i){auto f=burst.pop(400000+i*20000);assert(f&&f->bytes==payload(i));}
    assert(burst.resets==0&&burst.late==0&&burst.concealed==0&&burst.high_water==15);
    // Entire 400 ms chunks, delivered 400 ms apart, are valid clocked audio.
    RtpPlayout chunks;int read=0;
    for(int ms=0;ms<2500;ms+=20){
        if(ms%400==0&&ms<2000)for(int i=ms/20;i<ms/20+20;++i)chunks.push(i*960,4,payload(i),960,ms*1000);
        auto f=chunks.pop(ms*1000);
        if(f&&read<100){assert(f->bytes==payload(read));++read;}
    }
    assert(read==100&&chunks.resets==0&&chunks.overflow==0);
    // Jitter + reordering within 70 ms, including a 32-bit timestamp wrap.
    RtpPlayout jitter;read=0;std::vector<bool> delivered(120,false);
    for(int ms=0;ms<2900;ms+=20){
        for(int i=0;i<120;++i){int delay=i?((i*37)%71):0;
            if(!delivered[i]&&ms>=i*20+delay){
                jitter.push(uint32_t(0xffffa000u+i*960),2,payload(i),960,ms*1000);delivered[i]=true;
            }
        }
        auto f=jitter.pop(ms*1000);
        if(f&&read<120){assert(f->bytes==payload(read));++read;}
    }
    assert(read==120&&jitter.resets==0&&jitter.late==0);
    // A 220 ms delivery pause then a catch-up burst, observed on the device.
    RtpPlayout stall;read=0;delivered.assign(80,false);
    for(int ms=0;ms<2100&&read<80;ms+=20){
        for(int i=0;i<80;++i){int arrival=(i>=30&&i<=40)?820:i*20;
            if(!delivered[i]&&ms>=arrival){stall.push(i*960,3,payload(i),960,ms*1000);delivered[i]=true;}
        }
        auto f=stall.pop(ms*1000);if(f){assert(f->bytes==payload(read));++read;}
    }
    assert(read==80&&stall.concealed==0&&stall.late==0&&stall.resets==0);
    // If a receive pause exceeds the initial reserve, refill at the same RTP
    // position; do not replace valid delayed speech with a chain of fake PLC.
    RtpPlayout refill;read=0;delivered.assign(80,false);
    for(int ms=0;ms<3000&&read<80;ms+=20){
        for(int i=0;i<80;++i)if(!delivered[i]&&ms>=i*20+(i>=30?600:0)){
            refill.push(i*960,5,payload(i),960,ms*1000);delivered[i]=true;
        }
        auto f=refill.pop(ms*1000);if(f){assert(f->bytes==payload(read));++read;}
    }
    assert(read==80&&refill.late==0&&refill.concealed==0&&refill.rebuffers>0);
    // One actual lost packet gets exactly one PLC frame within the utterance.
    RtpPlayout loss;
    for(int i=0;i<20;++i)if(i!=5)loss.push(1000+i*960,1,payload(i),960,0);
    for(int i=0;i<20;++i){auto f=loss.pop(400000+i*20000);assert(f);assert(i==5?f->bytes.empty():f->bytes==payload(i));}
    assert(loss.concealed==1&&loss.resets==0);
    // A real DTX idle may restart without a run of hundreds of fake losses.
    loss.pop(2000000);loss.push(1000+1000*960,1,payload(42),960,3000000);
    assert(!loss.pop(3050000));auto resumed=loss.pop(3400000);assert(resumed&&resumed->bytes==payload(42));
    // Bounded backlog, as distinct from ordinary receive bursts.
    RtpPlayout bounded;
    for(int i=0;i<300;++i)bounded.push(i*960,1,payload(i),960,0);
    assert(bounded.depth()<=96&&bounded.overflow>0);
    // Complete transport -> PCM pipeline: generation clock falls behind by
    // 300 ms, then another 100 ms twice. Preserve pitch and every sample block
    // without inserting silence while gradually replenishing the reserve.
    RtpPlayout paced;SpeechTempo tempo;int next=0,decoded=0,rendered=0,gaps=0,scaled=0;
    short tone[1920],out[1920];double phase=0;int crossings=0;short previous=0;
    for(int ms=0;ms<19500;ms+=20){
        while(next<900){
            int arrival=next*20+(next>=100?300:0)+(next>=350?100:0)+(next>=600?100:0);
            if(arrival>ms)break;
            paced.push(next*960,7,payload(next),960,ms*1000);++next;
        }
        if(tempo.update(paced.buffered_ms()+tempo.available()/48)<.999f)++scaled;
        for(int batch=0;batch<6&&tempo.available()<960;++batch){
            auto f=paced.pop(ms*1000);if(!f)break;
            for(int i=0;i<960;++i){short value=std::sin(phase)*6000;phase+=2*3.141592653589793*200/48000;tone[2*i]=tone[2*i+1]=value;}
            tempo.write(tone,960);++decoded;
        }
        int count=tempo.read(out,960);
        if(ms>=500&&ms<18000){
            if(count<960)++gaps;
            for(int i=0;i<count;++i){if(previous<=0&&out[2*i]>0)++crossings;previous=out[2*i];}
            rendered+=count;
        }
    }
    assert(decoded==900&&paced.late==0&&paced.concealed==0&&gaps==0&&scaled>0);
    double hz=double(crossings)*48000/rendered;assert(hz>197&&hz<203);
    // Reset releases pending sound immediately; an idle flush preserves tail.
    tempo.write(tone,960);tempo.clear();assert(tempo.available()==0);
    // Sustained source clock at 0.87 real time, not just a one-off delivery
    // burst. This reproduced repeated mid-word gaps with a fixed water mark.
    RtpPlayout slow;SpeechTempo clocked;int source_frame=0,source_decoded=0,clock_gaps=0;
    for(int ms=0;ms<56000;ms+=20){
        while(source_frame<2500&&source_frame*23<=ms){
            slow.push(source_frame*960,8,payload(source_frame),960,ms*1000);++source_frame;
        }
        clocked.update(slow.buffered_ms()+clocked.available()/48);
        for(int i=0;i<6&&clocked.available()<960;i++){
            auto f=slow.pop(ms*1000);if(!f)break;
            clocked.write(tone,960);++source_decoded;
        }
        int n=clocked.available()>=960?clocked.read(out,960):0;
        if(ms>=600&&n<960)++clock_gaps;
    }
    assert(source_decoded>2300&&clock_gaps==0&&slow.late==0&&slow.concealed==0);
    auto rgb=rainbow_frame(0);assert((rgb==std::array<int,9>{136,0,0,0,136,0,0,0,136}));
    assert(indicator_frame(false,2)=="F 0 0 0 0 0 0 0 0 0\n");
    assert(rainbow_frame(0)!=rainbow_frame(1));
    for(int i=0;i<100;++i)for(int value:rainbow_frame(i*.1))assert(value>=0&&value<=160);
    short opening[1920],closing[1920];mode_cue_block(true,2,opening);mode_cue_block(false,2,closing);
    bool different=false,nonzero=false;
    for(int i=0;i<1920;++i){assert(std::abs(opening[i])<=2600);different|=opening[i]!=closing[i];nonzero|=opening[i]!=0;}
    assert(different&&nonzero);
    std::cout<<"audio: bursts, jitter, RTP wrap/loss/idle, adaptive clock at 0.87, no inserted silence, pitch, reset, rainbow and cues passed\n";
}
