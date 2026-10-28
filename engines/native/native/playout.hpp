#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <map>
#include <optional>
#include <vector>
#include <array>

// One 48 kHz RTP timeline. Network arrival bursts do not move the playhead.
// The caller consumes frames at the ALSA clock, not at the arrival rate.
class RtpPlayout {
public:
    struct Frame {std::vector<unsigned char> bytes;int samples=960;bool reset=false;};
    unsigned int received=0,late=0,concealed=0,resets=0,source_changes=0;
    unsigned int overflow=0,high_water=0,idle_restarts=0,rebuffers=0;
    int64_t maximum_gap_us=0,maximum_ahead_samples=0;
    double jitter_us=0;
private:
    bool metadata_only=false;
    std::map<int64_t,Frame> packets;
    int64_t reference=0,expected=0,due_us=0,last_arrival=0,last_transit=0;
    uint32_t source=0;bool active=false,codec_reset=false,have_transit=false,starved=false;
    unsigned int missing=0;
    std::array<std::array<int64_t,4>,2048> trace{};size_t trace_count=0;
    int64_t first_arrival=0,first_timestamp=0;
    // Startup reserve only. Once started, an empty read never inserts a timed
    // refill: the PCM tempo controller maintains the reserve continuously.
    int prefill_us()const{return 400000;}
public:
    explicit RtpPlayout(bool observe_only=false):metadata_only(observe_only){}
    size_t depth()const{return packets.size();}
    int buffered_ms()const{int samples=0;for(const auto &p:packets)samples+=p.second.samples;return samples/48;}
    bool idle(int64_t now)const{return packets.empty()&&have_transit&&now-last_arrival>=500000;}
    int target_ms()const{return prefill_us()/1000;}
    std::vector<std::array<int64_t,4>> timing()const{
        std::vector<std::array<int64_t,4>> result;
        for(size_t i=trace_count>trace.size()?trace_count-trace.size():0;i<trace_count;++i)result.push_back(trace[i%trace.size()]);return result;
    }
    void clear(){packets.clear();active=false;have_transit=false;codec_reset=true;missing=0;starved=false;}
    bool push(uint32_t timestamp,uint32_t ssrc,std::vector<unsigned char> bytes,int samples,int64_t now){
        if(samples<=0||samples>5760||bytes.empty()||bytes.size()>4096)return false;
        if(!active||ssrc!=source){
            if(active&&ssrc!=source){++source_changes;++resets;}
            else if(received)++idle_restarts;
            packets.clear();source=ssrc;reference=expected=timestamp;
            due_us=now+prefill_us();missing=0;active=true;codec_reset=true;have_transit=false;
        }
        int64_t extended=reference+int32_t(timestamp-uint32_t(reference));
        reference=std::max(reference,extended);
        if(have_transit){
            maximum_gap_us=std::max(maximum_gap_us,now-last_arrival);
            double delta=std::abs(double(now-extended*1000000/48000-last_transit));
            jitter_us+=(delta-jitter_us)/16;
        }
        last_transit=now-extended*1000000/48000;last_arrival=now;have_transit=true;
        if(!trace_count){first_arrival=now;first_timestamp=extended;}
        trace[trace_count++%trace.size()]={(now-first_arrival)/1000,(extended-first_timestamp)/48,(expected-first_timestamp)/48,int64_t(packets.size())};
        if(metadata_only){++received;expected=std::max(expected,extended+samples);return true;}
        if(extended<expected){++late;return false;}
        maximum_ahead_samples=std::max(maximum_ahead_samples,extended-expected);
        if(!packets.emplace(extended,Frame{std::move(bytes),samples,false}).second)return false;
        ++received;starved=false;
        // A normal 200–400 ms server burst is retained in full. Only a true
        // backlog above 1.2 s / 96 packets is trimmed, with an explicit count.
        bool trimmed=false;
        while(packets.size()>96||packets.rbegin()->first-packets.begin()->first>57600){
            packets.erase(packets.begin());++overflow;trimmed=true;
        }
        if(trimmed&&expected<packets.begin()->first){
            expected=packets.begin()->first;codec_reset=true;++resets;
        }
        high_water=std::max(high_water,static_cast<unsigned int>(packets.size()));return true;
    }
    std::optional<Frame> pop(int64_t now){
        if(!active||now<due_us)return {};
        auto found=packets.find(expected);
        if(found==packets.end()){
            if(!packets.empty()&&packets.begin()->first-expected>9600){
                // A DTX timestamp discontinuity does not require seconds of PLC.
                expected=packets.begin()->first;codec_reset=true;++resets;
                found=packets.begin();
            }else{
                if(packets.empty()&&now-last_arrival>=500000){active=false;return {};}
                // No packet is not proof of loss. Freeze RTP and accept the
                // next frame immediately. A 240 ms refill here used to turn a
                // single short arrival delay into an audible hole in a word.
                if(packets.empty()){if(!starved)++rebuffers;starved=true;return {};}
                // A later timestamp already queued proves a real missing slot.
                ++missing;++concealed;expected+=960;return Frame{{},960,false};
            }
        }
        Frame result=std::move(found->second);packets.erase(found);
        expected+=result.samples;missing=0;
        result.reset=codec_reset;codec_reset=false;return result;
    }
};
