#include "neteq_c.h"
#include <opus.h>
#include <nlohmann/json.hpp>
#include <algorithm>
#include <cassert>
#include <cmath>
#include <fstream>
#include <iostream>
#include <memory>

using json=nlohmann::json;
int main(int argc,char **argv){
    assert(argc>=2);json traces=json::array();
    for(int argument=1;argument<argc;++argument){
        json source;std::ifstream(argv[argument])>>source;auto trace=source.at("rtp_timing");assert(trace.size()>100);
        int error;auto encoder=opus_encoder_create(48000,1,OPUS_APPLICATION_VOIP,&error);assert(encoder&&error==0);
        opus_encoder_ctl(encoder,OPUS_SET_BITRATE(32000));opus_encoder_ctl(encoder,OPUS_SET_COMPLEXITY(3));
        auto engine=std::unique_ptr<AlisaNetEq,decltype(&alisa_neteq_destroy)>(alisa_neteq_create(80,600,0),alisa_neteq_destroy);assert(engine);
        int64_t origin=trace[0][0],origin_ts=trace[0][1],end=int64_t(trace.back()[0])-origin;
        size_t next=0;double phase=0;unsigned int rendered=0,zero_run=0,max_zero_run=0,blocks=0,crossings=0;short previous=0;
        for(int64_t ms=0;ms<=end;ms+=10){
            while(next<trace.size()&&int64_t(trace[next][0])-origin<=ms){
                short tone[960];for(short &value:tone){value=std::sin(phase)*6000;phase+=2*3.141592653589793*200/48000;}
                unsigned char packet[400];int size=opus_encode(encoder,tone,960,packet,sizeof(packet));assert(size>0);
                uint32_t ts=uint32_t(0xffffa000u+(int64_t(trace[next][1])-origin_ts)*48);
                assert(alisa_neteq_insert(engine.get(),uint16_t(65530+next),ts,9,packet,size)==0);++next;
            }
            short output[960];assert(alisa_neteq_render(engine.get(),output)==480);++blocks;
            if(ms>=500&&ms<end-500){
                int peak=0;for(short v:output)peak=std::max(peak,std::abs(int(v)));
                zero_run=peak==0?zero_run+10:0;max_zero_run=std::max(max_zero_run,zero_run);
                for(int i=0;i<480;++i){if(previous<=0&&output[2*i]>0)++crossings;previous=output[2*i];}rendered+=480;
            }
            alisa_neteq_advance(engine.get(),10000);
        }
        AlisaNetEqStats stats;alisa_neteq_stats(engine.get(),&stats);
        double hz=double(crossings)*48000/rendered;
        assert(stats.insert_errors==0&&stats.render_errors==0&&max_zero_run<150&&hz>193&&hz<207);
        traces.push_back({{"trace",argv[argument]},{"packets",next},{"output_blocks",blocks},{"longest_digital_zero_run_ms",max_zero_run},
            {"tone_frequency_hz",hz},{"target_ms",stats.target_delay_ms},{"current_ms",stats.current_delay_ms},
            {"concealed_ms",double(stats.concealed_samples)/48},{"inserted_ms",double(stats.inserted_samples)/48},
            {"removed_ms",double(stats.removed_samples)/48},{"interruptions",stats.interruption_count},
            {"render_errors",stats.render_errors},{"insert_errors",stats.insert_errors}});
        alisa_neteq_flush(engine.get());short after[960];int peak=0;
        for(int n=0;n<10;++n){assert(alisa_neteq_render(engine.get(),after)==480);for(short v:after)peak=std::max(peak,std::abs(int(v)));alisa_neteq_advance(engine.get(),10000);}
        traces.back()["peak_after_flush"]=peak;
        assert(peak==0);
        // A new RTP source may restart both sequence and timestamp. The old
        // decoder state must not strand the next answer behind its timeline.
        peak=0;
        for(int n=0;n<30;++n){
            short tone[960];for(short &v:tone){v=std::sin(phase)*6000;phase+=2*3.141592653589793*200/48000;}
            unsigned char packet[400];int size=opus_encode(encoder,tone,960,packet,sizeof(packet));assert(size>0);
            assert(alisa_neteq_insert(engine.get(),n,10000+n*960,99,packet,size)==0);
            for(int half=0;half<2;++half){assert(alisa_neteq_render(engine.get(),after)==480);
                for(short v:after)peak=std::max(peak,std::abs(int(v)));alisa_neteq_advance(engine.get(),10000);}
        }
        assert(peak>1000);traces.back()["new_ssrc_resumed"]=true;
        opus_encoder_destroy(encoder);
    }
    std::cout<<traces.dump(2)<<"\n";
}
