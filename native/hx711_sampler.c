/*
 * HX711 high-rate sampler for Orange Pi / Linux.
 *
 * The sampler intentionally does only raw acquisition. It emits newline-delimited
 * CSV records: unix_time_ns,sequence,raw_adc,status
 *
 * Backends:
 *   --mock                 deterministic synthetic stream for development
 *   --sysfs DATA SCK       Linux sysfs GPIO numbers for DATA(DOUT) and SCK(PD_SCK)
 *
 * Build: cc -O3 -std=c11 -Wall -Wextra -o build/hx711_sampler native/hx711_sampler.c
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t keep_running = 1;

static void on_signal(int signum) {
    (void)signum;
    keep_running = 0;
}

static int64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (int64_t)ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

static void sleep_us(long usec) {
    struct timespec req;
    req.tv_sec = usec / 1000000L;
    req.tv_nsec = (usec % 1000000L) * 1000L;
    nanosleep(&req, NULL);
}

static int write_text(const char *path, const char *value) {
    int fd = open(path, O_WRONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    ssize_t n = write(fd, value, strlen(value));
    close(fd);
    return n < 0 ? -1 : 0;
}

static int export_gpio(int gpio) {
    char path[128];
    snprintf(path, sizeof(path), "/sys/class/gpio/gpio%d", gpio);
    if (access(path, F_OK) == 0) return 0;
    char value[32];
    snprintf(value, sizeof(value), "%d", gpio);
    return write_text("/sys/class/gpio/export", value);
}

static int set_direction(int gpio, const char *direction) {
    char path[128];
    snprintf(path, sizeof(path), "/sys/class/gpio/gpio%d/direction", gpio);
    return write_text(path, direction);
}

static int set_value_fd(int fd, int value) {
    if (lseek(fd, 0, SEEK_SET) < 0) return -1;
    return write(fd, value ? "1" : "0", 1) == 1 ? 0 : -1;
}

static int read_value_fd(int fd) {
    char c = '1';
    if (lseek(fd, 0, SEEK_SET) < 0) return -1;
    if (read(fd, &c, 1) != 1) return -1;
    return c == '0' ? 0 : 1;
}

static int open_gpio_value(int gpio, int writable) {
    char path[128];
    snprintf(path, sizeof(path), "/sys/class/gpio/gpio%d/value", gpio);
    return open(path, writable ? (O_RDWR | O_CLOEXEC) : (O_RDONLY | O_CLOEXEC));
}

static int32_t sign_extend_24(uint32_t value) {
    if (value & 0x800000U) value |= 0xFF000000U;
    return (int32_t)value;
}

static int wait_ready(int data_fd, int timeout_us) {
    int waited = 0;
    while (keep_running && waited < timeout_us) {
        int v = read_value_fd(data_fd);
        if (v == 0) return 0;
        if (v < 0) return -1;
        sleep_us(20);
        waited += 20;
    }
    return 1;
}

static int read_hx711(int data_fd, int sck_fd, int gain_pulses, int32_t *out) {
    if (wait_ready(data_fd, 1000000) != 0) return -1;
    uint32_t value = 0;
    for (int i = 0; i < 24; ++i) {
        set_value_fd(sck_fd, 1);
        // Keep pulses short for maximum throughput; userspace GPIO still limits rate.
        value = (value << 1) | (uint32_t)(read_value_fd(data_fd) > 0);
        set_value_fd(sck_fd, 0);
    }
    for (int i = 0; i < gain_pulses; ++i) {
        set_value_fd(sck_fd, 1);
        set_value_fd(sck_fd, 0);
    }
    *out = sign_extend_24(value);
    return 0;
}

static int run_mock(double hz) {
    uint64_t seq = 0;
    const long period_us = (long)(1000000.0 / hz);
    while (keep_running) {
        double t = (double)seq / hz;
        double drift = 120.0 * sin(t / 45.0);
        double load = (fmod(t, 30.0) > 15.0) ? 50000.0 : 0.0;
        double noise = 260.0 * sin(t * 37.0) + 70.0 * sin(t * 113.0);
        int32_t raw = (int32_t)(8380000.0 + load + drift + noise);
        printf("%lld,%llu,%d,OK\n", (long long)now_ns(), (unsigned long long)seq++, raw);
        fflush(stdout);
        sleep_us(period_us > 0 ? period_us : 1000);
    }
    return 0;
}

static int run_sysfs(int data_gpio, int sck_gpio, int gain_pulses) {
    if (export_gpio(data_gpio) != 0 || export_gpio(sck_gpio) != 0) {
        fprintf(stderr, "failed to export GPIOs: %s\n", strerror(errno));
        return 2;
    }
    if (set_direction(data_gpio, "in") != 0 || set_direction(sck_gpio, "out") != 0) {
        fprintf(stderr, "failed to set GPIO direction: %s\n", strerror(errno));
        return 2;
    }
    int data_fd = open_gpio_value(data_gpio, 0);
    int sck_fd = open_gpio_value(sck_gpio, 1);
    if (data_fd < 0 || sck_fd < 0) {
        fprintf(stderr, "failed to open GPIO values: %s\n", strerror(errno));
        return 2;
    }
    set_value_fd(sck_fd, 0);
    uint64_t seq = 0;
    while (keep_running) {
        int32_t raw = 0;
        if (read_hx711(data_fd, sck_fd, gain_pulses, &raw) == 0) {
            printf("%lld,%llu,%d,OK\n", (long long)now_ns(), (unsigned long long)seq++, raw);
        } else {
            printf("%lld,%llu,0,NOT_READY\n", (long long)now_ns(), (unsigned long long)seq++);
        }
        fflush(stdout);
    }
    close(data_fd);
    close(sck_fd);
    return 0;
}

static void usage(const char *argv0) {
    fprintf(stderr,
            "Usage:\n"
            "  %s --mock [hz]\n"
            "  %s --sysfs DATA_GPIO SCK_GPIO [gain_pulses]\n"
            "gain_pulses: 1=A128, 2=B32, 3=A64 (default 1)\n",
            argv0, argv0);
}

int main(int argc, char **argv) {
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    setvbuf(stdout, NULL, _IOLBF, 0);
    if (argc >= 2 && strcmp(argv[1], "--mock") == 0) {
        double hz = argc >= 3 ? atof(argv[2]) : 80.0;
        if (hz <= 0.0) hz = 80.0;
        return run_mock(hz);
    }
    if (argc >= 4 && strcmp(argv[1], "--sysfs") == 0) {
        int data_gpio = atoi(argv[2]);
        int sck_gpio = atoi(argv[3]);
        int gain_pulses = argc >= 5 ? atoi(argv[4]) : 1;
        if (gain_pulses < 1 || gain_pulses > 3) gain_pulses = 1;
        return run_sysfs(data_gpio, sck_gpio, gain_pulses);
    }
    usage(argv[0]);
    return 1;
}
