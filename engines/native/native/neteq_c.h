#pragma once
#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif
typedef struct AlisaNetEq AlisaNetEq;
typedef struct {
    uint64_t packets,insert_errors,render_errors,total_samples,concealed_samples,silent_concealed_samples;
    uint64_t concealment_events,inserted_samples,removed_samples,discarded_packets,buffer_flushes;
    uint64_t delay_sum_ms,emitted_samples;
    uint32_t current_delay_ms,target_delay_ms,interruption_count,interruption_duration_ms;
} AlisaNetEqStats;
AlisaNetEq *alisa_neteq_create(int minimum_delay_ms,int maximum_delay_ms,int64_t simulated_start_us);
void alisa_neteq_destroy(AlisaNetEq *self);
int alisa_neteq_insert(AlisaNetEq *self,uint16_t sequence,uint32_t timestamp,uint32_t ssrc,
                       const unsigned char *payload,size_t bytes);
// Always one 10 ms / 480-frame block, interleaved stereo at 48 kHz.
int alisa_neteq_render(AlisaNetEq *self,int16_t *output);
void alisa_neteq_flush(AlisaNetEq *self);
void alisa_neteq_stats(AlisaNetEq *self,AlisaNetEqStats *stats);
void alisa_neteq_advance(AlisaNetEq *self,int64_t microseconds);
#ifdef __cplusplus
}
#endif
