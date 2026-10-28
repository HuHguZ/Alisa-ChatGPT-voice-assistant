/* A short local lease fails open if the controller stops, even after SIGKILL.
 * Atomically replace ASCII monotonic milliseconds; no stat ABI or wall clock.
 */
extern int rename(const char *, const char *);

static int tts_lease_refresh(const char *path) {
    if (!path) return 1;
    char temporary[256], value[48];
    int n = snprintf(temporary, sizeof(temporary), "%s.new", path);
    if (n <= 0 || (size_t)n >= sizeof(temporary)) return 0;
    struct timespec stamp;
    if (clock_gettime(1, &stamp) < 0) return 0;
    u64 milliseconds = (u64)stamp.sec * 1000 + (u64)stamp.nsec / 1000000;
    n = snprintf(value, sizeof(value), "TTS1 %llu\n", milliseconds);
    if (n <= 0 || (size_t)n >= sizeof(value)) return 0;
    int fd = open(temporary, 1 | 64 | 512 | 0400000 | 02000000, 0600);
    if (fd < 0) return 0;
    int good = write(fd, value, (size_t)n) == n;
    close(fd);
    if (good && rename(temporary, path) == 0) return 1;
    unlink(temporary);
    return 0;
}
