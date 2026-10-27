/* Authenticated Opus datagrams. No TCP data/ACK dependency on the audio path.
 * Loaded only into our aplay process; libc/ALSA come from that executable.
 * All packet/queue sizes are bounded. Only a key delivered over SSH can feed
 * the speaker. stdin EOF ends the worker; no persistent network daemon.
 */
typedef __SIZE_TYPE__ size_t;
typedef unsigned long frames_t;
extern void *dlsym(void *, const char *);
extern void *dlopen(const char *, int);
extern char *getenv(const char *);
extern long read(int, void *, size_t);
extern long write(int, const void *, size_t);
extern int close(int);
extern void _exit(int);
extern int *__errno_location(void);
extern int snprintf(char *, size_t, const char *, ...);
extern int sscanf(const char *, const char *, ...);
extern int socket(int, int, int);
extern int bind(int, const void *, unsigned int);
extern int getsockname(int, void *, unsigned int *);
extern long recv(int, void *, size_t, int);
extern int inet_pton(int, const char *, void *);
extern const char *inet_ntop(int, const void *, char *, unsigned int);
extern int fcntl(int, int, ...);
struct pollfd { int fd; short events, revents; };
extern int poll(struct pollfd *, unsigned long, int);
struct timespec { long sec, nsec; };
extern int clock_gettime(int, struct timespec *);
struct address { unsigned short family, port; unsigned int ip; char zero[8]; };

#define MAX_OPUS 400
#define SLOTS 32
struct frame { unsigned int seq; int size, redundant; unsigned char data[MAX_OPUS]; };
static struct frame ring[SLOTS];
static unsigned int received, invalid, late, duplicate, redundant, concealed;
static unsigned int played, underruns, resync;

static long long now_us(void) {
    struct timespec ts;
    if (clock_gettime(1, &ts) < 0) _exit(2);
    return (long long)ts.sec * 1000000 + ts.nsec / 1000;
}
static int transfer(int fd, void *data, size_t size) {
    unsigned char *p = data;
    while (size) {
        long n = read(fd, p, size);
        if (n < 0 && *__errno_location() == 4) continue;
        if (n <= 0) return -1;
        p += n; size -= (size_t)n;
    }
    return 0;
}
static void fail(void) {
    const char message[] = "Alice packet playback failed\n";
    write(2, message, sizeof(message)-1);
    _exit(2);
}
static int equal(const unsigned char *a, const unsigned char *b, int n) {
    unsigned int diff = 0;
    for (int i=0; i<n; ++i) diff |= a[i] ^ b[i];
    return diff == 0;
}
static unsigned int u32(const unsigned char *p) {
    return (unsigned int)p[0]<<24 | (unsigned int)p[1]<<16 |
           (unsigned int)p[2]<<8 | p[3];
}
static void insert(unsigned int seq, const unsigned char *p, int size,
                   int copy, unsigned int expected) {
    if ((int)(seq-expected) < 0) { if (!copy) ++late; return; }
    if (seq-expected >= SLOTS) return;
    struct frame *f = &ring[seq % SLOTS];
    if (f->size && f->seq == seq) { if (!copy) ++duplicate; return; }
    f->seq=seq; f->size=size; f->redundant=copy;
    for (int i=0; i<size; ++i) f->data[i]=p[i];
}
static void clear_ring(void) {
    for (int i=0; i<SLOTS; ++i) ring[i].size=0;
}

__attribute__((constructor)) static void run_worker(void) {
    const char *enabled = getenv("ALICE_PACKET_PLAYBACK");
    if (!enabled || enabled[0] != '1') return;
    const char *opus_path = getenv("ALICE_OPUS_LIBRARY");
    const char *crypto_path = getenv("ALICE_CRYPTO_LIBRARY");
    void *opus = dlopen(opus_path ? opus_path : "libopus.so.0", 2);
    void *crypto = dlopen(crypto_path ? crypto_path : "libcrypto.so.1.1", 2);
    if (!opus || !crypto) fail();
    void *(*decoder_create)(int,int,int *) = dlsym(opus,"opus_decoder_create");
    int (*decode)(void *,const unsigned char *,int,short *,int,int) = dlsym(opus,"opus_decode");
    void (*decoder_destroy)(void *) = dlsym(opus,"opus_decoder_destroy");
    void *(*ctx_new)(void) = dlsym(crypto,"EVP_CIPHER_CTX_new");
    void (*ctx_free)(void *) = dlsym(crypto,"EVP_CIPHER_CTX_free");
    void *(*cipher)(void) = dlsym(crypto,"EVP_aes_256_gcm");
    int (*decrypt_init)(void *,void *,void *,const void *,const void *) = dlsym(crypto,"EVP_DecryptInit_ex");
    int (*update)(void *,void *,int *,const void *,int) = dlsym(crypto,"EVP_DecryptUpdate");
    int (*final)(void *,void *,int *) = dlsym(crypto,"EVP_DecryptFinal_ex");
    int (*ctrl)(void *,int,int,void *) = dlsym(crypto,"EVP_CIPHER_CTX_ctrl");
    int (*pcm_open)(void **,const char *,int,int) = dlsym(0,"snd_pcm_open");
    int (*set_params)(void *,int,int,unsigned int,unsigned int,int,unsigned int) = dlsym(0,"snd_pcm_set_params");
    int (*get_params)(void *,frames_t *,frames_t *) = dlsym(0,"snd_pcm_get_params");
    long (*write_frames)(void *,const void *,frames_t) = dlsym(0,"snd_pcm_writei");
    int (*recover)(void *,int,int) = dlsym(0,"snd_pcm_recover");
    int (*drop)(void *) = dlsym(0,"snd_pcm_drop");
    int (*prepare)(void *) = dlsym(0,"snd_pcm_prepare");
    int (*close_pcm)(void *) = dlsym(0,"snd_pcm_close");
    int (*sw_malloc)(void **) = dlsym(0,"snd_pcm_sw_params_malloc");
    void (*sw_free)(void *) = dlsym(0,"snd_pcm_sw_params_free");
    int (*sw_current)(void *,void *) = dlsym(0,"snd_pcm_sw_params_current");
    int (*set_start)(void *,void *,frames_t) = dlsym(0,"snd_pcm_sw_params_set_start_threshold");
    int (*apply_sw)(void *,void *) = dlsym(0,"snd_pcm_sw_params");
    if (!decoder_create || !decode || !decoder_destroy || !ctx_new || !ctx_free ||
        !cipher || !decrypt_init || !update || !final || !ctrl || !pcm_open ||
        !set_params || !get_params || !write_frames || !recover || !drop ||
        !prepare || !close_pcm || !sw_malloc || !sw_free || !sw_current ||
        !set_start || !apply_sw) fail();

    /* Bind to the SSH server address, never a wildcard public interface. */
    char client_ip[64], server_ip[64]; unsigned int client_port, server_port;
    const char *binding = getenv("ALICE_PACKET_BIND");
    const char *connection = getenv("SSH_CONNECTION");
    if (!binding) {
        if (!connection || sscanf(connection,"%63s %u %63s %u",client_ip,
                &client_port,server_ip,&server_port) != 4) fail();
        binding = server_ip;
    }
    struct address address = {0}; address.family=2;
    if (inet_pton(2,binding,&address.ip) != 1) fail();
    int fd = socket(2,2|04000|02000000,0); /* nonblocking, close-on-exec */
    if (fd < 0 || bind(fd,&address,sizeof(address)) < 0) fail();
    unsigned int address_size=sizeof(address);
    if (getsockname(fd,&address,&address_size) < 0 ||
        !inet_ntop(2,&address.ip,server_ip,sizeof(server_ip))) fail();
    unsigned int port = ((unsigned char *)&address.port)[0]*256u +
                        ((unsigned char *)&address.port)[1];
    char message[512];
    int n = snprintf(message,sizeof(message),"{\"ready\":true,\"ip\":\"%s\",\"port\":%u}\n",server_ip,port);
    if (n <= 0 || write(1,message,(size_t)n) != n) fail();
    unsigned char secret[40];
    if (transfer(0,secret,sizeof(secret)) < 0) { close(fd); _exit(0); }
    int error=0;
    void *decoder=decoder_create(48000,2,&error), *ctx=ctx_new();
    void *pcm=0,*sw=0;
    frames_t buffer=0,period=0;
    const char *device=getenv("ALICE_PLAYBACK_DEVICE");
    if (!decoder || error || !ctx || pcm_open(&pcm,device?device:"default",0,0)<0 ||
        set_params(pcm,2,3,2,48000,1,80000)<0 || get_params(pcm,&buffer,&period)<0 ||
        !buffer || !period) fail();
    frames_t prefill=buffer>period?buffer-period:1;
    if (prefill>2880) prefill=2880;
    if (sw_malloc(&sw)<0 || sw_current(pcm,sw)<0 || set_start(pcm,sw,prefill)<0 ||
        apply_sw(pcm,sw)<0) fail();
    sw_free(sw);
    n=snprintf(message,sizeof(message),"ALICE_PACKET_CONFIG buffer=%lu period=%lu prefill=%lu jitter_ms=40\n",buffer,period,prefill);
    write(2,message,(size_t)n);
    /* Metadata must never block playback if SSH itself has a long stall. */
    int flags=fcntl(1,3);
    if (flags<0 || fcntl(1,4,flags|04000)<0) fail();
    unsigned int expected=0;
    int started=0;
    long long due=0, last_valid=now_us(), report_at=last_valid+1000000;
    struct pollfd fds[2]={{fd,1,0},{0,1,0}};
    short samples[960*2];
    while (1) {
        long long now=now_us();
        long long wait=started?due-now:20000;
        if (wait<0) wait=0;
        if (wait>20000) wait=20000;
        int polled=poll(fds,2,(int)((wait+999)/1000));
        if (polled<0) { if (*__errno_location()==4) continue; fail(); }
        if (fds[1].revents) break; /* SSH exit/parent death/explicit stop. */
        /* Drain a bounded batch before choosing the frame due now. */
        for (int batch=0; batch<64; ++batch) {
            unsigned char packet[1201], plain[4+2*MAX_OPUS];
            long count=recv(fd,packet,sizeof(packet),0x40);
            if (count<0) {
                if (*__errno_location()==11 || *__errno_location()==4) break;
                fail();
            }
            if (count<36 || count>32+(int)sizeof(plain) ||
                !equal(packet,(const unsigned char *)"AVP1",4) ||
                !equal(packet+4,secret+32,8)) { ++invalid; continue; }
            int size=0, tail=0;
            if (decrypt_init(ctx,cipher(),0,secret,packet+4)!=1 ||
                update(ctx,0,&size,packet,16)!=1 ||
                update(ctx,plain,&size,packet+16,(int)count-32)!=1 ||
                ctrl(ctx,0x11,16,packet+count-16)!=1 || /* SET_TAG */
                final(ctx,plain+size,&tail)!=1) { ++invalid; continue; }
            int current=plain[0]*256+plain[1], previous=plain[2]*256+plain[3];
            if (current<1 || current>MAX_OPUS || previous>MAX_OPUS ||
                size!=4+current+previous) { ++invalid; continue; }
            unsigned int seq=u32(packet+12);
            now=now_us(); last_valid=now; ++received;
            if (!started) { expected=seq; due=now+40000; started=1; }
            if ((int)(seq-expected)>6) {
                /* A restarted/stalled sender must not create an old backlog. */
                clear_ring(); expected=seq && previous?seq-1:seq;
                due=now+20000; ++resync;
                if (drop(pcm)<0 || prepare(pcm)<0) fail();
            }
            insert(seq,plain+4,current,0,expected);
            if (seq && previous) insert(seq-1,plain+4+current,previous,1,expected);
        }
        now=now_us();
        if (now-last_valid>5000000) fail();
        if (started && now>=due) {
            if (now-due>80000) {
                /* Bound latency even after a process scheduling pause. */
                while (now-due>20000) { ++expected; due+=20000; }
                ++resync;
                if (drop(pcm)<0 || prepare(pcm)<0) fail();
            }
            struct frame *f=&ring[expected%SLOTS];
            int have=f->size && f->seq==expected;
            int frames=decode(decoder,have?f->data:0,have?f->size:0,samples,960,0);
            if (frames!=960) {
                frames=decode(decoder,0,0,samples,960,0); have=0;
            }
            if (frames!=960) fail();
            if (!have) ++concealed;
            else if (f->redundant) ++redundant;
            f->size=0;
            frames_t offset=0; int attempts=0;
            while (offset<960) {
                long done=write_frames(pcm,samples+offset*2,960-offset);
                if (done<0) {
                    if (++attempts>2 || recover(pcm,(int)done,1)<0) fail();
                    ++underruns;
                    continue;
                }
                if (!done) fail();
                offset+=(frames_t)done;
            }
            ++played; ++expected; due+=20000;
        }
        if (now>=report_at) {
            n=snprintf(message,sizeof(message),
                "{\"received\":%u,\"played\":%u,\"redundant\":%u,\"concealed\":%u,"
                "\"late\":%u,\"duplicate\":%u,\"invalid\":%u,\"underruns\":%u,\"resync\":%u}\n",
                received,played,redundant,concealed,late,duplicate,invalid,underruns,resync);
            if (n>0 && n<(int)sizeof(message)) write(1,message,(size_t)n);
            report_at=now+1000000;
        }
    }
    drop(pcm); close_pcm(pcm); close(fd); ctx_free(ctx); decoder_destroy(decoder);
    _exit(0);
}
