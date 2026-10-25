/* Native command handling runs on the station. Only validated command labels
 * cross SSH; unrelated recognized text, startup credentials and PCM are never
 * logged. Run as an opt-in preload constructor in an existing libc executable.
 */
typedef __SIZE_TYPE__ size_t;
typedef unsigned long long u64;
extern char *getenv(const char *);
extern int socket(int, int, int);
extern int connect(int, const void *, unsigned int);
extern long read(int, void *, size_t);
extern long write(int, const void *, size_t);
extern int close(int);
extern int open(const char *, int, ...);
extern int unlink(const char *);
extern int flock(int, int);
extern int getpid(void);
extern int *__errno_location(void);
extern void *memcpy(void *, const void *, size_t);
extern void *memmove(void *, const void *, size_t);
extern int memcmp(const void *, const void *, size_t);
extern size_t strlen(const char *);
extern int strcmp(const char *, const char *);
extern int snprintf(char *, size_t, const char *, ...);
extern void _exit(int);
extern void (*signal(int, void (*)(int)))(int);
struct pollfd { int fd; short events, revents; };
struct timespec { long sec, nsec; };
struct local_address { unsigned short family, port; unsigned char address[4], pad[8]; };
extern int poll(struct pollfd *, unsigned long, int);
extern int clock_gettime(int, struct timespec *);

struct value { const unsigned char *data; size_t size; u64 number; int wire; };
struct agent {
    int fd, chat, recognizing, output_active, native_state;
    char listening[37], seen[64][37];
    unsigned int seen_count, commands, recognition_starts, speech_stops, native_speech_events, wake_blocks;
    double heartbeat;
};
static volatile int stopping;

static double seconds(void) {
    struct timespec t;
    if (clock_gettime(1, &t) < 0) return 0;
    return (double)t.sec + (double)t.nsec / 1000000000.0;
}

static int number(const unsigned char *data, size_t length, size_t *offset, u64 *out) {
    *out = 0;
    for (unsigned int shift = 0; shift < 70; shift += 7) {
        if (*offset >= length) return 0;
        unsigned char b = data[(*offset)++];
        if (shift == 63 && b > 1) return 0;
        *out |= (u64)(b & 127) << shift;
        if (!(b & 128)) return 1;
    }
    return 0;
}

static int field(const unsigned char *data, size_t length, unsigned int wanted, struct value *out) {
    size_t offset = 0;
    while (offset < length) {
        u64 key, n = 0;
        if (!number(data, length, &offset, &key) || !(key >> 3)) return 0;
        unsigned int wire = key & 7;
        const unsigned char *bytes = 0;
        if (wire == 0) {
            if (!number(data, length, &offset, &n)) return 0;
        } else if (wire == 1 || wire == 2 || wire == 5) {
            if (wire == 2) {
                if (!number(data, length, &offset, &n)) return 0;
            } else n = wire == 1 ? 8 : 4;
            if (n > length - offset) return 0;
            bytes = data + offset;
            offset += (size_t)n;
        } else return 0;
        if ((key >> 3) == wanted) {
            out->data = bytes;
            out->size = bytes ? (size_t)n : 0;
            out->number = wire == 0 ? n : 0;
            out->wire = wire;
            return 1;
        }
    }
    return wanted == 0;
}

static int bytes_field(const unsigned char *data, size_t size, unsigned int key, struct value *out) {
    return field(data, size, key, out) && out->wire == 2;
}

static u64 int_field(const unsigned char *data, size_t size, unsigned int key) {
    struct value v;
    return field(data, size, key, &v) && v.wire == 0 ? v.number : 0;
}

static size_t put_number(unsigned char *out, u64 value) {
    size_t size = 0;
    while (value >= 128) { out[size++] = (unsigned char)value | 128; value >>= 7; }
    out[size++] = (unsigned char)value;
    return size;
}

static size_t put_bytes(unsigned char *out, unsigned int key, const void *data, size_t length) {
    size_t n = put_number(out, key * 8 + 2);
    n += put_number(out + n, length);
    memcpy(out + n, data, length);
    return n + length;
}

static int output(int fd, const void *data, size_t size) {
    const unsigned char *p = data;
    while (size) {
        long n = write(fd, p, size);
        if (n < 0 && *__errno_location() == 4) continue;
        if (n <= 0) return 0;
        p += n; size -= (size_t)n;
    }
    return 1;
}

static int rpc(struct agent *a, const char *capability, unsigned int branch,
               const unsigned char *method, size_t size) {
    unsigned char remote[512], envelope[560], packet[564];
    size_t n = put_bytes(remote, 7, capability, strlen(capability));
    n += put_bytes(remote + n, branch, method, size);
    size_t length = put_bytes(envelope, 252, remote, n);
    length += put_number(envelope + length, 253 * 8);
    length += put_number(envelope + length, 210);
    packet[0] = length; packet[1] = length >> 8;
    packet[2] = length >> 16; packet[3] = length >> 24;
    memcpy(packet + 4, envelope, length);
    return output(a->fd, packet, length + 4);
}

static int alice_method(struct agent *a, unsigned char kind) {
    unsigned char method[] = {8, kind};
    return rpc(a, "AliceCapability", 8, method, sizeof(method));
}

static int pause_player(struct agent *a) {
    const char source_id[] = "00000000-0000-4000-8000-000000000001";
    unsigned char source[100] = {8,14,16,3,24,1}, method[130] = {8,1};
    size_t n = 6;
    n += put_bytes(source + n, 4, source_id, sizeof(source_id) - 1);
    n += put_bytes(source + n, 6, "ChatGPTVoice", 12);
    size_t length = 2 + put_bytes(method + 2, 5, source, n);
    method[length++] = 56; method[length++] = 0;
    return rpc(a, "PlaybackControlCapability", 13, method, length);
}

int alice_native_command(const unsigned char *text, size_t length) {
    unsigned char normalized[1025];
    size_t used = 0;
    int space = 1;
    for (size_t i = 0; i < length;) {
        unsigned int c = text[i++];
        if (c >= 0xc2 && c <= 0xdf && i < length && (text[i] & 0xc0) == 0x80)
            c = ((c & 31) << 6) | (text[i++] & 63);
        else if (c >= 128) return 0;
        if (c >= 0x410 && c <= 0x42f) c += 32;
        if (c == 0x401 || c == 0x451) c = 0x435;
        if (c >= 0x430 && c <= 0x44f) {
            if (used > sizeof(normalized) - 3) return 0;
            normalized[used++] = 0xc0 | (c >> 6);
            normalized[used++] = 0x80 | (c & 63);
            space = 0;
        } else if (!space && used < sizeof(normalized) - 1) {
            normalized[used++] = ' '; space = 1;
        }
    }
    if (space && used) --used;
    normalized[used] = 0;
    char *value = (char *)normalized;
    const char marker[] = "алиса ", please[] = " пожалуйста";
    if (used >= sizeof(marker) - 1 && !memcmp(value, marker, sizeof(marker) - 1)) {
        value += sizeof(marker) - 1; used -= sizeof(marker) - 1;
    }
    if (used >= sizeof(please) - 1 && !memcmp(value + used - sizeof(please) + 1,
                                             please, sizeof(please) - 1))
        value[used - sizeof(please) + 1] = 0;
    if (!strcmp(value, "открой чат")) return 1;
    if (!strcmp(value, "создай чат") || !strcmp(value, "создай новый чат")) return 2;
    if (!strcmp(value, "закрой чат") || !strcmp(value, "выйди из чата") ||
        !strcmp(value, "закончи разговор")) return 3;
    return 0;
}

static void status(struct agent *a, const char *event) {
    char text[512];
    int n = snprintf(text, sizeof(text),
        "{\"event\":\"%s\",\"pid\":%d,\"mode\":\"%s\",\"recognition_starts\":%u,"
        "\"commands\":%u,\"speech_stops\":%u,\"recognition_active\":%d,"
        "\"native_state\":%d,\"native_speech_events\":%u,\"output_active\":%d,\"wake_blocks\":%u}\n", event, getpid(),
        a->chat ? "chat" : "idle", a->recognition_starts, a->commands, a->speech_stops,
        a->recognizing, a->native_state, a->native_speech_events, a->output_active, a->wake_blocks);
    if (n > 0 && (size_t)n < sizeof(text) && !output(1, text, (size_t)n)) stopping = 1;
}

static void command_event(struct agent *a, int command) {
    const char *label = command == 1 ? "open" : command == 2 ? "new" : "close";
    char text[100];
    int n = snprintf(text, sizeof(text), "{\"event\":\"command\",\"action\":\"%s\"}\n", label);
    if (n > 0 && !output(1, text, (size_t)n)) stopping = 1;
    a->commands++;
}

static int handle_state(struct agent *a, const unsigned char *data, size_t size) {
    struct value request, identifier, text;
    int state = (int)int_field(data, size, 1);
    if (state == 2 && a->native_state != 2) a->native_speech_events++;
    a->native_state = state;
    char id[37] = {0};
    if (bytes_field(data, size, 7, &identifier) && identifier.size == 36)
        memcpy(id, identifier.data, 36);
    /* Keep the wake-triggered recognizer and its buffered speech intact.
     * Replacing it with START_RECOGNIZE can drop speech immediately after
     * the wake word and adds an unnecessary SDK session transition. */
    if (a->chat && state == 1 && id[0] && memcmp(id, a->listening, 36)) {
        memcpy(a->listening, id, sizeof(id));
        if (a->output_active) {
            a->recognizing = 0;
            a->wake_blocks++;
            memcpy(a->seen[a->seen_count++ % 64], id, sizeof(id));
            return alice_method(a, 3);
        }
        a->recognizing = 1;
        a->recognition_starts++;
    }
    if (a->chat && state == 2) {
        a->speech_stops++;
        if (!alice_method(a, 14)) return 0;
    }
    if (state == 0) a->recognizing = 0;
    if (!bytes_field(data, size, 15, &request) ||
        !field(request.data, request.size, 0, &text) ||
        int_field(request.data, request.size, 2) != 1 ||
        int_field(request.data, request.size, 4) != 1 || !id[0]) return 1;
    /* Wait for BUSY, as the stock request has then finished its voice input.
     * Cancelling an earlier LISTENING/final notification can strand the
     * spotter while the SDK is still completing that input. */
    if (state != 3) return 1;
    if (!bytes_field(request.data, request.size, 1, &text) || text.size > 1024) return 1;
    int action = alice_native_command(text.data, text.size);
    if (!action && !a->chat) return 1;
    for (unsigned int i = 0; i < a->seen_count && i < 64; ++i)
        if (!memcmp(a->seen[i], id, 36)) return 1;
    memcpy(a->seen[a->seen_count++ % 64], id, sizeof(id));
    /* Cancel the native answer locally, before a browser round trip. This
     * also isolates unrelated Alice requests while ChatGPT owns the speaker. */
    if (!alice_method(a, 3)) return 0;
    a->recognizing = 0;
    if (action) command_event(a, action);
    return 1;
}

static int handle_frame(struct agent *a, const unsigned char *data, size_t size) {
    struct value state;
    if (!field(data, size, 0, &state)) return 1;
    if (int_field(data, size, 253) != 3 || !bytes_field(data, size, 252, &state)) return 1;
    struct value ignored;
    if (!field(state.data, state.size, 0, &ignored)) return 1;
    return handle_state(a, state.data, state.size);
}

static void restore(struct agent *a) {
    if (a->recognizing) alice_method(a, 3);
    a->chat = a->recognizing = 0;
    a->listening[0] = 0;
    unlink("/data/opt/alice-voice/native-muted");
}

static int control(struct agent *a, const char *line) {
    a->heartbeat = seconds();
    if (!strcmp(line, "PING")) status(a, "status");
    else if (!strcmp(line, "CHAT")) { a->chat = 1; status(a, "mode"); }
    else if (!strcmp(line, "IDLE")) { restore(a); status(a, "mode"); }
    else if (!strcmp(line, "SPEAKING")) a->output_active = 1;
    else if (!strcmp(line, "QUIET")) a->output_active = 0;
    else if (!strcmp(line, "CANCEL")) return alice_method(a, 3);
    else if (!strcmp(line, "PAUSE")) {
        /* Native playback controls share SDK focus with speech input. Let
         * an in-flight request finish before enforcing the music pause. */
        if (a->native_state != 1 && a->native_state != 3) return pause_player(a);
    }
    else if (!strcmp(line, "STOP")) stopping = 1;
    return 1;
}

static void stop_signal(int unused) { (void)unused; stopping = 1; }

__attribute__((constructor)) static void run_agent(void) {
    const char *enabled = getenv("ALICE_NATIVE_AGENT");
    if (!enabled || enabled[0] != '1') return;
    signal(13, (void (*)(int))1);
    signal(15, stop_signal); signal(2, stop_signal);
    const char *lock_path = getenv("ALICE_NATIVE_LOCK");
    if (!lock_path) lock_path = "/data/opt/alice-voice/native-agent.lock";
    int lock = open(lock_path, 2 | 64, 0600);
    if (lock < 0 || flock(lock, 2 | 4) < 0) _exit(2);
    unsigned int port = 9875;
    const char *port_text = getenv("ALICE_NATIVE_PORT");
    if (port_text) {
        port = 0;
        while (*port_text >= '0' && *port_text <= '9') port = port * 10 + *port_text++ - '0';
        if (*port_text || !port || port > 65535) _exit(2);
    }
    struct local_address address =
        {2, (unsigned short)((port >> 8) | (port << 8)), {127,0,0,1}, {0}};
    struct agent a = {0}; a.fd = -1; a.native_state = -1;
    for (int attempt = 0; attempt < 40 && !stopping; ++attempt) {
        a.fd = socket(2, 1, 0);
        if (a.fd >= 0 && connect(a.fd, &address, sizeof(address)) == 0) break;
        if (a.fd >= 0) close(a.fd);
        a.fd = -1; poll(0, 0, 250);
    }
    if (a.fd < 0) _exit(2);
    a.heartbeat = seconds(); status(&a, "ready");
    static unsigned char input[512 * 1024 + 4];
    size_t used = 0, control_used = 0;
    char line[64];
    int good = 1;
    while (!stopping && good) {
        if (seconds() - a.heartbeat > 8) {
            restore(&a); status(&a, "heartbeat_timeout"); break;
        }
        struct pollfd fds[2] = {{0,1,0}, {a.fd,1,0}};
        int ready = poll(fds, 2, 200);
        if (ready < 0) { if (*__errno_location() == 4) continue; break; }
        if (fds[0].revents & (1 | 8 | 16)) {
            char data[128]; long n = read(0, data, sizeof(data));
            if (n <= 0) break;
            for (long i = 0; i < n && good; ++i) {
                if (data[i] == '\n') {
                    line[control_used] = 0; good = control(&a, line); control_used = 0;
                } else if (control_used < sizeof(line) - 1) line[control_used++] = data[i];
                else { good = 0; break; }
            }
        }
        if (fds[1].revents & (1 | 8 | 16)) {
            long n = read(a.fd, input + used, sizeof(input) - used);
            if (n <= 0) break;
            used += (size_t)n;
            while (used >= 4 && good) {
                size_t length = (size_t)input[0] | (size_t)input[1] << 8 |
                                (size_t)input[2] << 16 | (size_t)input[3] << 24;
                if (!length || length > sizeof(input) - 4) { good = 0; break; }
                if (used < length + 4) break;
                good = handle_frame(&a, input + 4, length);
                used -= length + 4;
                memmove(input, input + length + 4, used);
            }
        }
    }
    restore(&a); close(a.fd); close(lock);
    _exit(good ? 0 : 2);
}
