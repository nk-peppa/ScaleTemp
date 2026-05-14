/*
 * HX711 high-rate sampler for Orange Pi / Linux.
 *
 * The sampler intentionally does only raw acquisition. It emits newline-delimited
 * CSV records: unix_time_ns,sequence,raw_adc,status
 *
 * Backends:
 *   --wiringpi DATA SCK    wiringPi pin numbers for DATA(DOUT) and SCK(PD_SCK)
 *                           (default: DATA=5, SCK=1 to match the original script)
 *   --sysfs DATA SCK       Linux sysfs GPIO numbers for DATA(DOUT) and SCK(PD_SCK)
 *   --mock                 deterministic synthetic stream for development
 */
#define _POSIX_C_SOURCE 200809L
#include <dlfcn.h>
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

#define DEFAULT_WIRINGPI_DATA 5
#define DEFAULT_WIRINGPI_SCK 1
#define WPI_INPUT 0
#define WPI_OUTPUT 1
#define WPI_PUD_UP 2

static volatile sig_atomic_t keep_running = 1;

typedef int (*wiringPiSetup_fn)(void);
typedef void (*pinMode_fn)(int, int);
typedef void (*pullUpDnControl_fn)(int, int);
typedef int (*digitalRead_fn)(int);
typedef void (*digitalWrite_fn)(int, int);
typedef void (*delay_fn)(unsigned int);

typedef struct WiringPiApi {
    void *handle;
    wiringPiSetup_fn wiringPiSetup;
    pinMode_fn pinMode;
    pullUpDnControl_fn pullUpDnControl;
    digitalRead_fn digitalRead;
    digitalWrite_fn digitalWrite;
    delay_fn delay;
} WiringPiApi;

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

static void nop_delay(void) {
    volatile int d;
    for (d = 0; d < 10; d++) __asm__ volatile("");
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
    int rc = write_text("/sys/class/gpio/export", value);
    sleep_us(100000);
    return rc;
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

static int read_hx711_sysfs(int data_fd, int sck_fd, int gain_pulses, int32_t *out) {
    if (wait_ready(data_fd, 1000000) != 0) return -1;
    uint32_t value = 0;
    for (int i = 0; i < 24; ++i) {
        set_value_fd(sck_fd, 1);
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

static int load_wiringpi(WiringPiApi *api) {
    const char *libs[] = {"libwiringPi.so", "libwiringPi.so.2", "libwiringPi.so.3", NULL};
    memset(api, 0, sizeof(*api));
    for (int i = 0; libs[i] != NULL && api->handle == NULL; ++i) {
        api->handle = dlopen(libs[i], RTLD_LAZY);
    }
    if (!api->handle) {
        fprintf(stderr, "failed to load wiringPi shared library: %s\n", dlerror());
        return -1;
    }
#define LOAD_SYM(name) do { \
    api->name = (name##_fn)dlsym(api->handle, #name); \
    if (!api->name) { fprintf(stderr, "missing wiringPi symbol %s\n", #name); return -1; } \
} while (0)
    LOAD_SYM(wiringPiSetup);
    LOAD_SYM(pinMode);
    LOAD_SYM(pullUpDnControl);
    LOAD_SYM(digitalRead);
    LOAD_SYM(digitalWrite);
    LOAD_SYM(delay);
#undef LOAD_SYM
    return 0;
}

static int read_hx711_wiringpi(WiringPiApi *api, int data_pin, int sck_pin, int gain_pulses, int32_t *out) {
    uint32_t value = 0;
    int waited = 0;
    while (keep_running && api->digitalRead(data_pin) == 1 && waited < 2000000) {
        waited++;
    }
    if (waited >= 2000000) return -1;

    for (int i = 0; i < 24; ++i) {
        api->digitalWrite(sck_pin, 1);
        nop_delay();
        value = (value << 1) | (uint32_t)(api->digitalRead(data_pin) > 0);
        api->digitalWrite(sck_pin, 0);
        nop_delay();
    }
    for (int i = 0; i < gain_pulses; ++i) {
        api->digitalWrite(sck_pin, 1);
        nop_delay();
        api->digitalWrite(sck_pin, 0);
        nop_delay();
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

static int run_wiringpi(int data_pin, int sck_pin, int gain_pulses) {
    WiringPiApi api;
    if (load_wiringpi(&api) != 0) return 2;
    if (api.wiringPiSetup() == -1) {
        fprintf(stderr, "wiringPi init failed\n");
        return 2;
    }
    api.pinMode(sck_pin, WPI_OUTPUT);
    api.pinMode(data_pin, WPI_INPUT);
    api.pullUpDnControl(data_pin, WPI_PUD_UP);
    api.digitalWrite(sck_pin, 0);
    fprintf(stderr, "HX711 wiringPi backend active: DATA_PIN=%d SCK_PIN=%d gain_pulses=%d\n", data_pin, sck_pin, gain_pulses);

    uint64_t seq = 0;
    while (keep_running) {
        int32_t raw = 0;
        if (read_hx711_wiringpi(&api, data_pin, sck_pin, gain_pulses, &raw) == 0 && raw != 0 && raw != 0xFFFFFF) {
            printf("%lld,%llu,%d,OK\n", (long long)now_ns(), (unsigned long long)seq++, raw);
        } else {
            printf("%lld,%llu,0,NOT_READY\n", (long long)now_ns(), (unsigned long long)seq++);
            api.delay(10);
        }
        fflush(stdout);
    }
    if (api.handle) dlclose(api.handle);
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
    fprintf(stderr, "HX711 sysfs backend active: DATA_GPIO=%d SCK_GPIO=%d gain_pulses=%d\n", data_gpio, sck_gpio, gain_pulses);
    uint64_t seq = 0;
    while (keep_running) {
        int32_t raw = 0;
        if (read_hx711_sysfs(data_fd, sck_fd, gain_pulses, &raw) == 0) {
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
            "  %s --wiringpi [DATA_PIN SCK_PIN [gain_pulses]]\n"
            "  %s --sysfs DATA_GPIO SCK_GPIO [gain_pulses]\n"
            "  %s --mock [hz]\n"
            "Defaults match original wiringPi script: DATA=5, SCK=1, gain=1\n"
            "gain_pulses: 1=A128, 2=B32, 3=A64 (default 1)\n",
            argv0, argv0, argv0);
}

int main(int argc, char **argv) {
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    setvbuf(stdout, NULL, _IOLBF, 0);

    if (argc == 1) {
        return run_wiringpi(DEFAULT_WIRINGPI_DATA, DEFAULT_WIRINGPI_SCK, 1);
    }
    if (argc >= 2 && strcmp(argv[1], "--mock") == 0) {
        double hz = argc >= 3 ? atof(argv[2]) : 80.0;
        if (hz <= 0.0) hz = 80.0;
        return run_mock(hz);
    }
    if (argc >= 2 && strcmp(argv[1], "--wiringpi") == 0) {
        int data_pin = argc >= 4 ? atoi(argv[2]) : DEFAULT_WIRINGPI_DATA;
        int sck_pin = argc >= 4 ? atoi(argv[3]) : DEFAULT_WIRINGPI_SCK;
        int gain_pulses = argc >= 5 ? atoi(argv[4]) : 1;
        if (gain_pulses < 1 || gain_pulses > 3) gain_pulses = 1;
        return run_wiringpi(data_pin, sck_pin, gain_pulses);
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
