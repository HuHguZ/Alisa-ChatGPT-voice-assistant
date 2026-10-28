// Physical ALSA test with local synthesized speech and a real packet timing trace.
// Never opens a microphone or makes a network request.
#include "neteq_c.h"
#include "alsa.hpp"
#include <opus.h>
#include <nlohmann/json.hpp>
#include <algorithm>
#include <chrono>
#include <fstream>
#include <iostream>
#include <memory>
#include <thread>
#include <vector>
#include <sys/resource.h>

using json=nlohmann::json;
using Clock=std::chrono::steady_clock;
double cpu_seconds(){rusage u{};getrusage(RUSAGE_SELF,&u);return u.ru_utime.tv_sec+u.ru_stime.tv_sec+(u.ru_utime.tv_usec+u.ru_stime.tv_usec)/1e6;}
int main(int argc,char **argv){
    if(argc!=3)return 2;
    json source;std::ifstream(argv[1])>>source;auto trace=source.at("rtp_timing");
    std::ifstream raw(argv[2],std::ios::binary);std::vector<short> pcm;
    short value;while(raw.read(reinterpret_cast<char *>(&value),2))pcm.push_back(value);
    if(pcm.size()<48000||trace.size()<100)return 2;
    struct Packet{int64_t at;uint32_t timestamp;std::vector<unsigned char> bytes;};std::vector<Packet> packets;
    int error;auto encoder=opus_encoder_create(48000,1,OPUS_APPLICATION_VOIP,&error);if(!encoder||error)return 2;
    opus_encoder_ctl(encoder,OPUS_SET_BITRATE(32000));opus_encoder_ctl(encoder,OPUS_SET_COMPLEXITY(3));
    // Encode before the measured playout interval; production receives Opus.
    size_t frames=(pcm.size()+959)/960;int64_t origin=trace[0][0],origin_ts=trace[0][1];
    for(size_t i=0;i<frames&&i<trace.size();++i){
        short input[960]{};for(size_t j=0;j<960&&i*960+j<pcm.size();++j)input[j]=pcm[i*960+j];
        unsigned char payload[400];int n=opus_encode(encoder,input,960,payload,sizeof(payload));if(n<=0)return 2;
        packets.push_back({int64_t(trace[i][0])-origin,uint32_t(0xffffa000u+(int64_t(trace[i][1])-origin_ts)*48),
                           std::vector<unsigned char>(payload,payload+n)});
    }
    opus_encoder_destroy(encoder);
    auto engine=std::unique_ptr<AlisaNetEq,decltype(&alisa_neteq_destroy)>(alisa_neteq_create(80,600,-1),alisa_neteq_destroy);
    if(!engine)return 2;PcmOutput output("default");
    unsigned int audible=0,max_render_us=0,loop_overruns=0;size_t next=0;
    auto start=Clock::now(),tick=start;double cpu_start=cpu_seconds();int64_t end=packets.back().at+1000;
    for(int64_t ms=0;ms<=end;ms+=20){
        if(Clock::now()-tick>std::chrono::milliseconds(30))++loop_overruns;
        while(next<packets.size()&&packets[next].at<=ms){auto &p=packets[next];
            if(alisa_neteq_insert(engine.get(),uint16_t(65530+next),p.timestamp,9,p.bytes.data(),p.bytes.size()))return 2;++next;}
        short samples[1920];auto render_start=Clock::now();
        for(int half=0;half<2;++half)if(alisa_neteq_render(engine.get(),samples+half*960)!=480)return 2;
        max_render_us=std::max(max_render_us,unsigned(std::chrono::duration_cast<std::chrono::microseconds>(Clock::now()-render_start).count()));
        long long power=0;for(short v:samples)power+=int(v)*int(v);if(power>1920LL*128*128)++audible;
        // Same +6 dB ceiling as the client, bounded synthetic speech.
        for(short &v:samples)v=std::clamp(int(v)*2,-31128,31128);
        output.write(samples,960);tick+=std::chrono::milliseconds(20);std::this_thread::sleep_until(tick);
    }
    output.drain();double elapsed=std::chrono::duration<double>(Clock::now()-start).count();
    AlisaNetEqStats n{};alisa_neteq_stats(engine.get(),&n);rusage u{};getrusage(RUSAGE_SELF,&u);
    bool passed=audible>20&&output.underruns==0&&n.insert_errors==0&&n.render_errors==0;
    std::cout<<json{{"passed",passed},{"physical_alsa",true},{"synthetic_speech",true},{"microphone_opened",false},
        {"cloud_used",false},{"elapsed_seconds",elapsed},{"cpu_one_core_percent",(cpu_seconds()-cpu_start)/elapsed*100},
        {"max_rss_kib",u.ru_maxrss},{"max_render_us",max_render_us},{"loop_overruns",loop_overruns},
        {"audible_blocks",audible},{"alsa_underruns",output.underruns},{"insert_errors",n.insert_errors},{"render_errors",n.render_errors},
        {"neteq_interruptions",n.interruption_count},{"concealed_samples",n.concealed_samples},
        {"silent_concealed_samples",n.silent_concealed_samples},{"target_delay_ms",n.target_delay_ms}}.dump(2)<<"\n";
    return passed?0:2;
}
