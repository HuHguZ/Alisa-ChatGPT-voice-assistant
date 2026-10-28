#include "neteq_c.h"
#include "api/audio/audio_frame.h"
#include "api/audio_codecs/opus_audio_decoder_factory.h"
#include "api/audio_codecs/audio_format.h"
#include "api/neteq/neteq.h"
#include "api/rtp_headers.h"
#include "modules/audio_coding/neteq/default_neteq_factory.h"
#include "rtc_base/logging.h"
#include "system_wrappers/include/clock.h"
#include <algorithm>
#include <memory>

struct AlisaNetEq {
    std::unique_ptr<webrtc::SimulatedClock> simulation;
    std::unique_ptr<webrtc::NetEq> engine;
    webrtc::AudioFrame frame;
    uint32_t last_ssrc=0;bool have_ssrc=false;
    uint64_t packets=0,insert_errors=0,render_errors=0;
};

extern "C" AlisaNetEq *alisa_neteq_create(int minimum_delay_ms,int maximum_delay_ms,int64_t simulated_start_us){
    rtc::LogMessage::LogToDebug(rtc::LS_NONE);
    auto self=std::make_unique<AlisaNetEq>();
    webrtc::Clock *clock=webrtc::Clock::GetRealTimeClock();
    if(simulated_start_us>=0){self->simulation=std::make_unique<webrtc::SimulatedClock>(simulated_start_us);clock=self->simulation.get();}
    webrtc::NetEq::Config config;config.sample_rate_hz=48000;config.min_delay_ms=minimum_delay_ms;
    config.max_delay_ms=maximum_delay_ms;config.max_packets_in_buffer=96;config.enable_muted_state=true;
    self->engine=webrtc::DefaultNetEqFactory().CreateNetEq(config,webrtc::CreateOpusAudioDecoderFactory(),clock);
    if(!self->engine||!self->engine->RegisterPayloadType(111,webrtc::SdpAudioFormat("opus",48000,2)))return nullptr;
    return self.release();
}
extern "C" void alisa_neteq_destroy(AlisaNetEq *self){delete self;}
extern "C" int alisa_neteq_insert(AlisaNetEq *self,uint16_t sequence,uint32_t timestamp,uint32_t ssrc,const unsigned char *payload,size_t bytes){
    if(!self||!payload||!bytes||bytes>4096)return -1;
    if(self->have_ssrc&&self->last_ssrc!=ssrc)self->engine->FlushBuffers();
    self->have_ssrc=true;self->last_ssrc=ssrc;
    webrtc::RTPHeader header{};header.sequenceNumber=sequence;header.timestamp=timestamp;header.ssrc=ssrc;header.payloadType=111;
    int result=self->engine->InsertPacket(header,rtc::ArrayView<const uint8_t>(payload,bytes));
    ++self->packets;if(result)++self->insert_errors;return result;
}
extern "C" int alisa_neteq_render(AlisaNetEq *self,int16_t *output){
    if(!self||!output)return -1;
    bool muted=false;int result=self->engine->GetAudio(&self->frame,&muted);
    std::fill(output,output+960,0);
    if(result||self->frame.sample_rate_hz_!=48000||self->frame.samples_per_channel_!=480){++self->render_errors;return -1;}
    if(!muted){
        const int16_t *data=self->frame.data();
        if(self->frame.num_channels_==2)std::copy(data,data+960,output);
        else if(self->frame.num_channels_==1)for(int i=0;i<480;++i)output[i*2]=output[i*2+1]=data[i];
        else {++self->render_errors;return -1;}
    }
    return 480;
}
extern "C" void alisa_neteq_flush(AlisaNetEq *self){if(self)self->engine->FlushBuffers();}
extern "C" void alisa_neteq_advance(AlisaNetEq *self,int64_t microseconds){if(self&&self->simulation)self->simulation->AdvanceTimeMicroseconds(microseconds);}
extern "C" void alisa_neteq_stats(AlisaNetEq *self,AlisaNetEqStats *s){
    if(!self||!s)return;
    auto a=self->engine->GetLifetimeStatistics();auto b=self->engine->GetOperationsAndState();
    *s={};s->packets=self->packets;s->insert_errors=self->insert_errors;s->render_errors=self->render_errors;
    s->total_samples=a.total_samples_received;s->concealed_samples=a.concealed_samples;s->silent_concealed_samples=a.silent_concealed_samples;
    s->concealment_events=a.concealment_events;s->inserted_samples=a.inserted_samples_for_deceleration;s->removed_samples=a.removed_samples_for_acceleration;
    s->discarded_packets=a.packets_discarded;s->buffer_flushes=b.packet_buffer_flushes;s->delay_sum_ms=a.jitter_buffer_delay_ms;
    s->emitted_samples=a.jitter_buffer_emitted_count;s->current_delay_ms=self->engine->FilteredCurrentDelayMs();s->target_delay_ms=self->engine->TargetDelayMs();
    s->interruption_count=a.interruption_count;s->interruption_duration_ms=a.total_interruption_duration_ms;
}
