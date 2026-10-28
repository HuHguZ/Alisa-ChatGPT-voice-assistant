/* Copy the already processed vqe_0 output from the stock engine. The original
 * function, its buffers and its return value are preserved. This ABI was
 * checked against the pinned Lite 2 library in bridge.py; no code addresses or
 * firmware patching are used. Never block the assistant on a slow receiver.
 */
typedef __SIZE_TYPE__ size_t;
extern void *dlsym(void *, const char *);
extern char *getenv(const char *);
extern int socket(int, int, int);
extern long sendto(int, const void *, size_t, int, const void *, unsigned int);
extern long write(int, const void *, size_t);
extern int *__errno_location(void);
extern void _exit(int);

struct unix_address { unsigned short family; char path[108]; };
static void *(*next_output)(void *, unsigned int, unsigned int, const float **, size_t *);
static struct unix_address address;
static unsigned int address_length;
static int tap_socket = -1, initialized;

static void initialize(void) {
    initialized = 1;
    const char *path = getenv("ALICE_CAPTURE_SOCKET");
    if (!path || !path[0]) return; /* Opt in only through the bridge profile. */
    unsigned int n = 0;
    while (path[n] && n < sizeof(address.path) - 1) {
        address.path[n] = path[n];
        ++n;
    }
    if (path[n]) return;
    address.path[n] = 0;
    address.family = 1;
    address_length = (unsigned int)sizeof(address.family) + n + 1;
    tap_socket = socket(1, 2 | 04000 | 02000000, 0);
    if (tap_socket >= 0) {
        const char text[] = "ALICE_VQE_TAP channel=vqe_0 type=6 index=0 rate=16000 format=s16le\n";
        /* One short startup diagnostic. PCM and recognized words are not logged. */
        write(2, text, sizeof(text) - 1);
    }
}

void *YandexVqeC_GetOutputChannelData(void *engine, unsigned int kind,
                                    unsigned int index, const float **data,
                                    size_t *frames) {
    int entered_errno = *__errno_location();
    if (!next_output) next_output = dlsym((void *)(long)-1, "YandexVqeC_GetOutputChannelData");
    *__errno_location() = entered_errno;
    if (!next_output) _exit(2); /* The pinned original symbol must exist. */
    void *error = next_output(engine, kind, index, data, frames);
    int original_errno = *__errno_location();
    /* AudioInputChannelType VQE=6, channel 0 is named vqe_0 by the stock
     * device. Raw microphones, feedback, BNR and other zones are not exported.
     * Its normalized float32 samples are converted like the stock consumer
     * (scale 32768), with explicit saturation and NaN handling.
     */
    if (!error && kind == 6 && index == 0 && data && frames && *data &&
        *frames > 0 && *frames <= 4096) {
        if (!initialized) initialize();
        if (tap_socket >= 0) {
            short packet[160]; /* At most 10 ms of mono 16 kHz per datagram. */
            for (size_t offset = 0; offset < *frames;) {
                size_t count = *frames - offset;
                if (count > 160) count = 160;
                for (size_t i = 0; i < count; ++i) {
                    float value = (*data)[offset + i];
                    packet[i] = value != value ? 0 : value >= 1.0f ? 32767 :
                                value <= -1.0f ? -32768 : (short)(value * 32768.0f);
                }
                sendto(tap_socket, packet, count * sizeof(short), 0x40 | 0x4000,
                       &address, address_length);
                offset += count;
            }
        }
    }
    *__errno_location() = original_errno;
    return error;
}
