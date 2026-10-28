#include "playout.hpp"
#include "tempo.hpp"
#include <nlohmann/json.hpp>
#include <fstream>
#include <iostream>

int main(int argc,char **argv){
    if(argc!=2)return 2;
    nlohmann::json data;std::ifstream(argv[1])>>data;
    auto trace=data.at("rtp_timing");if(trace.empty())return 2;
    int64_t start=trace[0][0],timestamp=trace[0][1],end=int64_t(trace.back()[0])-start;
    RtpPlayout queue;SpeechTempo tempo;size_t next=0;int read=0,gaps=0,blocks=0,minimum=1000;
    int64_t first_gap=-1;short pcm[1920],out[1920];double phase=0;
    for(int64_t ms=0;ms<=end;ms++){
        while(next<trace.size()&&int64_t(trace[next][0])-start<=ms){
            uint32_t ts=(int64_t(trace[next][1])-timestamp)*48;
            queue.push(ts,1,{1},960,ms*1000);++next;
        }
        if(ms%20)continue;
        minimum=std::min(minimum,int(tempo.update(queue.buffered_ms()+tempo.available()/48)*1000));
        for(int i=0;i<6&&tempo.available()<960;i++){
            auto f=queue.pop(ms*1000);if(!f)break;
            for(int j=0;j<960;j++){short v=std::sin(phase)*6000;phase+=2*3.141592653589793*200/48000;pcm[2*j]=pcm[2*j+1]=v;}
            tempo.write(pcm,960);++read;
        }
        int n=tempo.available()>=960?tempo.read(out,960):0;
        if(ms>=500){++blocks;if(n<960){++gaps;if(first_gap<0)first_gap=ms;}}
    }
    std::cout<<nlohmann::json{{"trace_packets",trace.size()},{"span_ms",end},{"decoded",read},
        {"late",queue.late},{"concealed",queue.concealed},{"rendered_blocks",blocks},
        {"starvation_blocks",gaps},{"first_gap_ms",first_gap},{"minimum_tempo_permille",minimum}}.dump(2)<<"\n";
    return gaps?1:0;
}
