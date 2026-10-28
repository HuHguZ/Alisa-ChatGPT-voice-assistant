#pragma once
#include "common.hpp"
#include <rtc/rtc.hpp>
#include <array>
#include <vector>

// Header/timing telemetry only: no payload, SDP, addresses or identifiers.
// Receiver-report correction remains opt-in for isolated synthetic probes.
class TransportAudit:public rtc::RtcpReceivingSession {
    uint32_t local_ssrc;bool correct_reports;
    std::mutex audit_mutex;
    unsigned int sender_reports=0,receiver_reports=0,bad_report_ssrc=0,bad_report_sequence=0;
    bool have_origin=false;uint64_t origin_ntp=0;uint32_t origin_rtp=0;
    Clock::time_point origin_time{};
    std::vector<std::array<int64_t,3>> clock_trace;
public:
    TransportAudit(uint32_t local,bool correct):local_ssrc(local),correct_reports(correct){}
    void incoming(rtc::message_vector &messages,const rtc::message_callback &send)override{
        for(const auto &message:messages){
            if(message->type!=rtc::Message::Control)continue;
            size_t offset=0;
            while(offset+4<=message->size()){
                const auto *p=reinterpret_cast<const unsigned char *>(message->data()+offset);
                size_t bytes=(size_t(p[2])*256+p[3]+1)*4;
                if((p[0]>>6)!=2||bytes<4||bytes>message->size()-offset)break;
                if(p[1]==200&&bytes>=28){
                    auto read32=[](const unsigned char *v){return uint32_t(v[0])<<24|uint32_t(v[1])<<16|uint32_t(v[2])<<8|v[3];};
                    uint64_t ntp=uint64_t(read32(p+8))<<32|read32(p+12);uint32_t rtp=read32(p+16);
                    auto now=Clock::now();std::lock_guard<std::mutex> lock(audit_mutex);
                    if(!have_origin){origin_ntp=ntp;origin_rtp=rtp;origin_time=now;have_origin=true;}
                    ++sender_reports;
                    if(clock_trace.size()<90)clock_trace.push_back({
                        std::chrono::duration_cast<std::chrono::milliseconds>(now-origin_time).count(),
                        int64_t(int32_t(rtp-origin_rtp))*1000/48000,
                        int64_t(double(ntp-origin_ntp)*(1000.0/4294967296.0))});
                }
                offset+=bytes;
            }
        }
        rtc::RtcpReceivingSession::incoming(messages,[&](rtc::message_ptr message){
            if(message->type==rtc::Message::Control&&message->size()>=8){
                auto *p=reinterpret_cast<unsigned char *>(message->data());
                if(p[1]==201){
                    auto *rr=reinterpret_cast<rtc::RtcpRr *>(p);
                    std::lock_guard<std::mutex> lock(audit_mutex);++receiver_reports;
                    if(rr->senderSSRC()!=local_ssrc)++bad_report_ssrc;
                    for(unsigned int i=0;i<(p[0]&31)&&8+(i+1)*24<=message->size();++i){
                        auto *block=rr->getReportBlock(i);
                        if(block->extendedHighestSeqNo()!=mCycles+mMaxSeq)++bad_report_sequence;
                        if(correct_reports)block->setSeqNo(mMaxSeq,uint16_t(mCycles>>16));
                    }
                    if(correct_reports)rr->setSenderSSRC(local_ssrc);
                }
            }
            send(std::move(message));
        });
    }
    json stats(){std::lock_guard<std::mutex> lock(audit_mutex);
        return {{"sender_reports",sender_reports},{"receiver_reports",receiver_reports},
            {"upstream_bad_report_ssrc",bad_report_ssrc},{"upstream_bad_report_sequence",bad_report_sequence},
            {"correct_reports_enabled",correct_reports},{"sender_clock_trace",clock_trace}};
    }
};
