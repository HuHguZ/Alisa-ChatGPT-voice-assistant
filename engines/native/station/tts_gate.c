/* Only the private speech PCM is silenced; GPT, files and music keep their
 * own handles. Config redirection is read-only and opt-in for mediad.
 */
typedef __SIZE_TYPE__ size_t;
typedef unsigned long frames_t;
typedef long result_t;
typedef unsigned long long u64;
extern void *dlsym(void *, const char *);
extern char *getenv(const char *);
extern int strcmp(const char *, const char *);
extern int open(const char *, int, ...);
extern long read(int, void *, size_t);
extern long write(int, const void *, size_t);
extern int close(int);
extern int *__errno_location(void);
extern void _exit(int);
struct timespec { long sec, nsec; };
extern int clock_gettime(int, struct timespec *);

static int muted(void) {
    const char *path = getenv("ALICE_TTS_GATE_FILE");
    if (!path) return 0;
    int fd = open(path, 04000 | 0400000 | 02000000);
    if (fd < 0) return 0;
    char value[48]; long n = read(fd, value, sizeof(value));
    close(fd);
    if (n < 7 || n >= (long)sizeof(value) || value[0] != 'T' || value[1] != 'T' ||
        value[2] != 'S' || value[3] != '1' || value[4] != ' ') return 0;
    u64 stamp = 0;
    for (long i = 5; i < n - 1; ++i) {
        if (value[i] < '0' || value[i] > '9' || stamp > 1000000000000ULL) return 0;
        stamp = stamp * 10 + value[i] - '0';
    }
    if (value[n - 1] != '\n') return 0;
    struct timespec now;
    if (clock_gettime(1, &now) < 0) return 0;
    u64 current = (u64)now.sec * 1000 + (u64)now.nsec / 1000000;
    return current >= stamp && current - stamp < 3000;
}

void *fopen64(const char *path, const char *mode) {
    static void *(*original)(const char *, const char *);
    if (!original) original = dlsym((void *)(long)-1, "fopen64");
    if (!original) _exit(2);
    const char *replacement = getenv("ALICE_QUASAR_CONFIG");
    int read_only = mode[0] == 'r';
    for (const char *p = mode; *p; ++p) if (*p == '+') read_only = 0;
    if (replacement && read_only &&
        !strcmp(path, "/system/vendor/quasar/quasar.cfg")) path = replacement;
    return original(path, mode);
}

result_t snd_pcm_writei(void *pcm, const void *data, frames_t frames) {
    int entered_errno = *__errno_location();
    static result_t (*original)(void *, const void *, frames_t);
    static const char *(*name)(void *);
    static result_t (*frames_to_bytes)(void *, result_t);
    static int last_muted = -1;
    if (!original) original = dlsym((void *)(long)-1, "snd_pcm_writei");
    if (!name) name = dlsym((void *)(long)-1, "snd_pcm_name");
    if (!frames_to_bytes) frames_to_bytes = dlsym((void *)(long)-1, "snd_pcm_frames_to_bytes");
    if (!original || !name || !frames_to_bytes) _exit(2);
    const char *device = name(pcm);
    if (!device || strcmp(device, "alice_native_tts")) {
        *__errno_location() = entered_errno;
        return original(pcm, data, frames);
    }
    int silence = muted() && frames_to_bytes(pcm, 1) == 4;
    if (__atomic_exchange_n(&last_muted, silence, __ATOMIC_RELAXED) != silence) {
        const char on[] = "ALICE_TTS_GATE muted=1\n", off[] = "ALICE_TTS_GATE muted=0\n";
        write(2, silence ? on : off, sizeof(on) - 1);
        const char *status = getenv("ALICE_TTS_GATE_STATUS");
        if (status) {
            int fd = open(status, 1 | 64 | 512 | 0400000 | 02000000, 0600);
            if (fd >= 0) { write(fd, silence ? on : off, sizeof(on) - 1); close(fd); }
        }
    }
    if (silence && frames) {
        /* The isolated sink explicitly negotiates stereo S16LE at 48 kHz. */
        unsigned char zero[16384] = {0};
        if (frames > sizeof(zero) / 4) frames = sizeof(zero) / 4;
        *__errno_location() = entered_errno;
        return original(pcm, zero, frames);
    }
    *__errno_location() = entered_errno;
    return original(pcm, data, frames);
}
