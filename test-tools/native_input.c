/* Temporary native-ASR fixture. Only generated test PCM is read from a file.
 * The ordinary build never includes this wrapper. Capture parameters and the
 * stock speaker-feedback channel stay unchanged. Both microphone paths are
 * isolated from room sound during this explicit test build: native ASR hears
 * generated files, and the web call receives generated input through PipeWire.
 */
#define snd_pcm_readi bridge_readi
#define sendto synthetic_bridge_sendto
#include "../station/capture_tap.c"
#undef snd_pcm_readi
#undef sendto
extern long sendto(int, const void *, size_t, int, const void *, unsigned int);
long synthetic_bridge_sendto(int fd, const void *data, size_t length, int flags,
                            const void *destination, unsigned int address_size) {
    (void)data;
    static unsigned char silence[960];
    if (length > sizeof(silence)) return -1;
    return sendto(fd, silence, length, flags, destination, address_size);
}
extern int open(const char *, int, ...);
extern long read(int, void *, size_t);
extern int close(int);
extern int unlink(const char *);
static int test_fd = -1;
static frames_t test_frames;

result_t snd_pcm_readi(void *pcm, void *buffer, frames_t frames) {
    result_t received = bridge_readi(pcm, buffer, frames);
    if (received <= 0 || !capture_ok || pcm_stream(pcm) != 1) return received;
    unsigned int second = channels >= 8 ? 2 : (channels > 1 ? 1 : 0);
    sample_t *native = buffer, packet[480];
    for (frames_t i = 0; i < (frames_t)received; ++i)
        native[i * channels] = native[i * channels + second] = 0;
    if (test_fd < 0) {
        const char *path = getenv("ALICE_SYNTHETIC_NATIVE_INPUT");
        if (!path || !path[0]) path = "/data/opt/alice-voice/synthetic-native-test.pcm";
        test_fd = open(path, 0);
        if (test_fd < 0) return received;
        unlink(path);
        test_frames = 0;
    }
    frames_t offset = 0;
    while (offset < (frames_t)received && test_fd >= 0) {
        frames_t count = (frames_t)received - offset;
        if (count > 480) count = 480;
        long size = read(test_fd, packet, count * sizeof(sample_t));
        if (size <= 0 || test_frames >= 48000 * 10) {
            close(test_fd);
            test_fd = -1;
            break;
        }
        count = (frames_t)size / sizeof(sample_t);
        for (frames_t i = 0; i < count; ++i) {
            native[(offset + i) * channels] = packet[i];
            native[(offset + i) * channels + second] = packet[i];
        }
        offset += count;
        test_frames += count;
    }
    return received;
}
