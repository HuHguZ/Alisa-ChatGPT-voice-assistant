/* Run inside an existing ALSA executable, so its ARM libc/ALSA dependencies
 * are resolved normally. Each 20 ms block is acknowledged only after ALSA
 * accepts it. The sender never queues an unbounded SSH window of old audio.
 */
typedef __SIZE_TYPE__ size_t;
typedef unsigned long frames_t;
typedef signed long result_t;
extern void *dlsym(void *, const char *);
extern char *getenv(const char *);
extern long read(int, void *, size_t);
extern long write(int, const void *, size_t);
extern int *__errno_location(void);
extern void _exit(int);
extern int snprintf(char *, size_t, const char *, ...);

static int transfer(int fd, void *data, size_t count, int output) {
    unsigned char *p = data;
    while (count) {
        long n = output ? write(fd, p, count) : read(fd, p, count);
        if (n < 0 && *__errno_location() == 4) continue;
        if (n <= 0) return -1;
        p += n;
        count -= (size_t)n;
    }
    return 0;
}

static void fail(void) {
    char message[] = "Alice playback worker failed\n";
    transfer(2, message, sizeof(message) - 1, 1);
    _exit(2);
}

__attribute__((constructor)) static void run_worker(void) {
    const char *enabled = getenv("ALICE_PLAYBACK_WORKER");
    if (!enabled || enabled[0] != '1') return;
    int (*pcm_open)(void **, const char *, int, int) = dlsym(0, "snd_pcm_open");
    int (*set_params)(void *, int, int, unsigned int, unsigned int, int,
                      unsigned int) = dlsym(0, "snd_pcm_set_params");
    int (*get_params)(void *, frames_t *, frames_t *) = dlsym(0, "snd_pcm_get_params");
    result_t (*write_frames)(void *, const void *, frames_t) = dlsym(0, "snd_pcm_writei");
    int (*recover)(void *, int, int) = dlsym(0, "snd_pcm_recover");
    int (*drop)(void *) = dlsym(0, "snd_pcm_drop");
    int (*close_pcm)(void *) = dlsym(0, "snd_pcm_close");
    int (*sw_malloc)(void **) = dlsym(0, "snd_pcm_sw_params_malloc");
    void (*sw_free)(void *) = dlsym(0, "snd_pcm_sw_params_free");
    int (*sw_current)(void *, void *) = dlsym(0, "snd_pcm_sw_params_current");
    int (*set_start)(void *, void *, frames_t) = dlsym(0, "snd_pcm_sw_params_set_start_threshold");
    int (*apply_sw)(void *, void *) = dlsym(0, "snd_pcm_sw_params");
    if (!pcm_open || !set_params || !get_params || !write_frames || !recover || !drop ||
        !close_pcm || !sw_malloc || !sw_free || !sw_current || !set_start ||
        !apply_sw) fail();
    void *pcm = 0, *sw = 0;
    frames_t buffer_frames = 0, period_frames = 0;
    const char *device = getenv("ALICE_PLAYBACK_DEVICE");
    if (!device || !device[0]) device = "default";
    /* S16_LE, RW_INTERLEAVED, stereo, 48 kHz, 80 ms jitter allowance. */
    if (pcm_open(&pcm, device, 0, 0) < 0 ||
        set_params(pcm, 2, 3, 2, 48000, 1, 80000) < 0 ||
        get_params(pcm, &buffer_frames, &period_frames) < 0 ||
        !buffer_frames || !period_frames) fail();
    /* Start with up to 60 ms already buffered, also after EPIPE recovery.
     * Clamp to actual ALSA geometry: a threshold larger than the buffer can
     * leave playback permanently prepared. Keep one period of write headroom.
     * The buffer size stays unchanged; the sender bounds its in-flight window.
     */
    frames_t prefill = buffer_frames > period_frames ? buffer_frames - period_frames : 1;
    if (prefill > 2880) prefill = 2880;
    if (sw_malloc(&sw) < 0 || sw_current(pcm, sw) < 0 ||
        set_start(pcm, sw, prefill) < 0 || apply_sw(pcm, sw) < 0) fail();
    sw_free(sw);
    char diagnostic[160];
    int length = snprintf(diagnostic, sizeof(diagnostic),
        "ALICE_PLAYBACK_CONFIG buffer=%lu period=%lu prefill=%lu\n",
        buffer_frames, period_frames, prefill);
    if (length > 0 && (size_t)length < sizeof(diagnostic))
        transfer(2, diagnostic, (size_t)length, 1);
    char ready[] = "READY\n";
    if (transfer(1, ready, sizeof(ready) - 1, 1) < 0) fail();
    short samples[960 * 2];
    while (transfer(0, samples, sizeof(samples), 0) == 0) {
        frames_t offset = 0;
        int recovered = 0;
        while (offset < 960) {
            result_t n = write_frames(pcm, samples + offset * 2, 960 - offset);
            if (n < 0) {
                if (recover(pcm, (int)n, 1) < 0) fail();
                length = snprintf(diagnostic, sizeof(diagnostic),
                    "ALICE_PLAYBACK_RECOVER error=%ld\n", n);
                if (length > 0 && (size_t)length < sizeof(diagnostic))
                    transfer(2, diagnostic, (size_t)length, 1);
                recovered = 1;
                continue;
            }
            if (n == 0) fail();
            offset += (frames_t)n;
        }
        char ack = recovered ? 'U' : 'K';
        if (transfer(1, &ack, 1, 1) < 0) break;
    }
    drop(pcm);
    close_pcm(pcm);
    _exit(0);
}
