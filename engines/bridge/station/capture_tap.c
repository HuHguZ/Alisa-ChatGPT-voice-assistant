/* Copy captured samples after ALSA has delivered them to the stock assistant.
 * Datagram delivery is nonblocking: a missing/slow receiver cannot stall ALSA.
 * No device, firmware partition, capture parameters, or ALSA config is changed.
 * Prototypes use the public ALSA/libc ABI so this file can be cross-built without
 * installing a second glibc sysroot on the workstation.
 */
typedef __SIZE_TYPE__ size_t;
typedef signed short sample_t;
typedef unsigned long frames_t;
typedef signed long result_t;
extern void *dlsym(void *, const char *);
extern char *getenv(const char *);
extern int socket(int, int, int);
extern int access(const char *, int);
extern long sendto(int, const void *, size_t, int, const void *, unsigned int);

struct unix_address { unsigned short family; char path[108]; };
static result_t (*next_readi)(void *, void *, frames_t);
static int (*pcm_stream)(void *);
static int (*params_malloc)(void **);
static void (*params_free)(void *);
static int (*params_current)(void *, void *);
static int (*get_channels)(void *, unsigned int *);
static int (*get_rate)(void *, unsigned int *, int *);
static int (*get_format)(void *, int *);
static void *capture_pcm;
static unsigned int channels;
static int capture_ok;
static int tap_socket = -1;
static struct unix_address address;
static unsigned int address_length;
static const char *native_mute_file;

static void resolve_functions(void) {
    void *next = (void *)(long)-1;
    next_readi = dlsym(next, "snd_pcm_readi");
    pcm_stream = dlsym(next, "snd_pcm_stream");
    params_malloc = dlsym(next, "snd_pcm_hw_params_malloc");
    params_free = dlsym(next, "snd_pcm_hw_params_free");
    params_current = dlsym(next, "snd_pcm_hw_params_current");
    get_channels = dlsym(next, "snd_pcm_hw_params_get_channels");
    get_rate = dlsym(next, "snd_pcm_hw_params_get_rate");
    get_format = dlsym(next, "snd_pcm_hw_params_get_format");
}

static void configure_capture(void *pcm) {
    void *params = 0;
    unsigned int rate = 0;
    int direction = 0, format = -1;
    capture_pcm = pcm;
    capture_ok = 0;
    native_mute_file = getenv("ALICE_NATIVE_MUTE_FILE");
    if (!params_malloc || !params_free || !params_current || !get_channels ||
        !get_rate || !get_format || params_malloc(&params) < 0) return;
    if (params_current(pcm, params) == 0 && get_channels(params, &channels) == 0 &&
        get_rate(params, &rate, &direction) == 0 && get_format(params, &format) == 0)
        capture_ok = rate == 48000 && format == 2 && channels >= 1 && channels <= 8;
    params_free(params);
    if (!capture_ok || tap_socket >= 0) return;
    const char *path = getenv("ALICE_CAPTURE_SOCKET");
    if (!path || !path[0]) path = "/data/opt/alice-voice/microphone.sock";
    unsigned int n = 0;
    while (path[n] && n < sizeof(address.path) - 1) {
        address.path[n] = path[n];
        ++n;
    }
    if (path[n]) { capture_ok = 0; return; }
    address.path[n] = 0;
    address.family = 1; /* AF_UNIX */
    address_length = (unsigned int)sizeof(address.family) + n + 1;
    /* SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC */
    tap_socket = socket(1, 2 | 04000 | 02000000, 0);
}

result_t snd_pcm_readi(void *pcm, void *buffer, frames_t frames) {
    if (!next_readi) resolve_functions();
    if (!next_readi) return -38; /* ENOSYS; the original symbol must exist. */
    result_t received = next_readi(pcm, buffer, frames);
    if (received <= 0 || !pcm_stream || pcm_stream(pcm) != 1) return received;
    if (capture_pcm != pcm) configure_capture(pcm);
    if (!capture_ok || tap_socket < 0) return received;
    const sample_t *input = buffer;
    /* The Lite 2 stock ALSA route returns mic0, mic1, speaker feedback.
     * A direct 8-channel capture instead places the microphones at 0 and 2.
     */
    unsigned int second = channels >= 8 ? 2 : (channels > 1 ? 1 : 0);
    sample_t packet[480];
    frames_t offset = 0;
    while (offset < (frames_t)received) {
        frames_t count = (frames_t)received - offset;
        if (count > 480) count = 480;
        for (frames_t i = 0; i < count; ++i) {
            frames_t pos = (offset + i) * channels;
            packet[i] = (sample_t)(((int)input[pos] + (int)input[pos + second]) / 2);
        }
        /* MSG_DONTWAIT | MSG_NOSIGNAL; dropping is preferable to backpressure. */
        sendto(tap_socket, packet, count * sizeof(sample_t), 0x40 | 0x4000,
               &address, address_length);
        offset += count;
    }
    /* Keep the bridge's original microphone samples, but temporarily give the
     * stock recognizer silence while the dedicated browser is using voice.
     * Preserve speaker feedback and every capture parameter.
     */
    if (native_mute_file && access(native_mute_file, 0) == 0) {
        sample_t *native = buffer;
        for (frames_t i = 0; i < (frames_t)received; ++i) {
            native[i * channels] = 0;
            native[i * channels + second] = 0;
        }
    }
    return received;
}
