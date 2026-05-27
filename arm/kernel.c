/* ============================================
 * POKE OS ARM64 — Kernel
 * UART (console + network) + Audio + Code Execution
 * Target: QEMU virt machine (aarch64)
 * ============================================ */

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long u64;

/* ── PL011 UART0 (console: 0x09000000) ── */
#define UART0_BASE 0x09000000
#define UART0_DR   (*(volatile u32 *)(UART0_BASE + 0x00))
#define UART0_FR   (*(volatile u32 *)(UART0_BASE + 0x18))

/* ── PL011 UART1 (network: 0x09040000) ── */
#define UART1_BASE 0x09040000
#define UART1_DR   (*(volatile u32 *)(UART1_BASE + 0x00))
#define UART1_FR   (*(volatile u32 *)(UART1_BASE + 0x18))
#define UART1_IBRD (*(volatile u32 *)(UART1_BASE + 0x24))
#define UART1_FBRD (*(volatile u32 *)(UART1_BASE + 0x28))
#define UART1_LCR  (*(volatile u32 *)(UART1_BASE + 0x2C))
#define UART1_CR   (*(volatile u32 *)(UART1_BASE + 0x30))

/* ── Console UART ── */
void uart_putc(char c) {
    while (UART0_FR & (1 << 5));
    UART0_DR = c;
}

void uart_print(const char *s) {
    while (*s) {
        if (*s == '\n') uart_putc('\r');
        uart_putc(*s++);
    }
}

void uart_hex(u64 val) {
    const char hex[] = "0123456789ABCDEF";
    uart_print("0x");
    for (int i = 60; i >= 0; i -= 4)
        uart_putc(hex[(val >> i) & 0xF]);
}

void uart_hex32(u32 val) {
    const char hex[] = "0123456789ABCDEF";
    for (int i = 28; i >= 0; i -= 4)
        uart_putc(hex[(val >> i) & 0xF]);
}

void uart_dec(u64 val) {
    char buf[20];
    int i = 0;
    if (val == 0) { uart_putc('0'); return; }
    while (val > 0) { buf[i++] = '0' + (val % 10); val /= 10; }
    while (i > 0) uart_putc(buf[--i]);
}

char uart_getc(void) {
    if (UART0_FR & (1 << 4)) return 0;
    return UART0_DR & 0xFF;
}

/* ── Network UART (serial ↔ TCP bridge) ── */
void net_init(void) {
    /* UART1 is configured by QEMU, just enable */
    UART1_CR = 0;           /* disable */
    UART1_LCR = (3 << 5);  /* 8N1 */
    UART1_CR = (1 << 0) | (1 << 8) | (1 << 9); /* enable + TX + RX */
}

void net_putc(char c) {
    while (UART1_FR & (1 << 5));
    UART1_DR = c;
}

void net_write(const u8 *data, int len) {
    for (int i = 0; i < len; i++) net_putc(data[i]);
}

void net_print(const char *s) {
    while (*s) net_putc(*s++);
}

int net_available(void) {
    return !(UART1_FR & (1 << 4));
}

u8 net_getc(void) {
    return UART1_DR & 0xFF;
}

int net_read(u8 *buf, int maxlen) {
    int n = 0;
    while (n < maxlen && net_available()) {
        buf[n++] = net_getc();
    }
    return n;
}

/* ── Memory utils ── */
void mem_copy(void *dst, const void *src, int n) {
    u8 *d = dst; const u8 *s = src;
    while (n--) *d++ = *s++;
}

void mem_set(void *dst, u8 val, int n) {
    u8 *d = dst;
    while (n--) *d++ = val;
}

int str_eq(const char *a, const char *b) {
    while (*a && *b) { if (*a++ != *b++) return 0; }
    return *a == *b;
}

int str_len(const char *s) {
    int n = 0; while (*s++) n++; return n;
}

/* ── Code Execution ── */
#define CODE_BUF_SIZE 4096
static u8 code_buf[CODE_BUF_SIZE] __attribute__((aligned(4096)));

#define RESULT_BUF_SIZE 256
static char result_buf[RESULT_BUF_SIZE];
static int result_len = 0;

/* Forward declarations */
void play_tone(u16 freq, u16 duration_ms);

/* ── Network Protocol ── */
/*
 * Simple serial protocol:
 *   Request:  "POKE" + len(4 LE) + payload
 *   Response: "RESP" + len(4 LE) + payload
 *
 *   Payload types:
 *     "EXEC" + code_bytes     → execute, return x0
 *     "PING"                  → return "PONG"
 *     "INFO"                  → return device info
 *     "TONE" + freq(2) + dur(2) → play tone
 */

#define NET_BUF_SIZE 8192
static u8 net_buf[NET_BUF_SIZE];
static int net_buf_len = 0;

void net_send_response(const char *data, int len) {
    net_putc('R'); net_putc('E'); net_putc('S'); net_putc('P');
    net_putc(len & 0xFF);
    net_putc((len >> 8) & 0xFF);
    net_putc((len >> 16) & 0xFF);
    net_putc((len >> 24) & 0xFF);
    net_write((const u8 *)data, len);
}

void handle_net_packet(u8 *payload, int len) {
    if (len >= 4 && payload[0]=='P' && payload[1]=='I' && payload[2]=='N' && payload[3]=='G') {
        uart_print("[NET] PING → PONG\n");
        net_send_response("PONG", 4);
        return;
    }

    if (len >= 4 && payload[0]=='I' && payload[1]=='N' && payload[2]=='F' && payload[3]=='O') {
        uart_print("[NET] INFO\n");
        const char *info = "{\"arch\":\"aarch64\",\"status\":\"alive\",\"caps\":[\"compute\",\"audio\"]}";
        net_send_response(info, str_len(info));
        return;
    }

    if (len >= 4 && payload[0]=='E' && payload[1]=='X' && payload[2]=='E' && payload[3]=='C') {
        int code_len = len - 4;
        if (code_len > CODE_BUF_SIZE) code_len = CODE_BUF_SIZE;
        mem_copy(code_buf, payload + 4, code_len);

        uart_print("[NET] EXEC ");
        uart_dec(code_len);
        uart_print(" bytes\n");

        /* Execute ARM64 code — return value in x0 */
        mem_set(result_buf, 0, RESULT_BUF_SIZE);
        result_len = 0;

        u64 (*fn)(char *, int *) = (u64 (*)(char *, int *))code_buf;
        u64 ret = fn(result_buf, &result_len);

        uart_print("[NET] x0=");
        uart_dec(ret);
        uart_print("\n");

        /* Build response */
        char resp[128];
        mem_set(resp, 0, 128);
        int rlen = 0;

        if (result_len > 0) {
            mem_copy(resp, result_buf, result_len);
            rlen = result_len;
        } else {
            /* Return x0 as decimal string */
            const char *p = "x0=";
            while (*p) resp[rlen++] = *p++;
            char dbuf[20]; int di = 0;
            u64 v = ret;
            if (v == 0) dbuf[di++] = '0';
            else { while (v > 0) { dbuf[di++] = '0' + (v % 10); v /= 10; } }
            while (di > 0) resp[rlen++] = dbuf[--di];
            resp[rlen++] = '\n';
        }

        net_send_response(resp, rlen);
        return;
    }

    if (len >= 4 && payload[0]=='T' && payload[1]=='O' && payload[2]=='N' && payload[3]=='E') {
        if (len >= 8) {
            u16 freq = payload[4] | (payload[5] << 8);
            u16 dur_ms = payload[6] | (payload[7] << 8);
            uart_print("[AUDIO] tone ");
            uart_dec(freq);
            uart_print("Hz ");
            uart_dec(dur_ms);
            uart_print("ms\n");
            play_tone(freq, dur_ms);
            net_send_response("OK", 2);
        }
        return;
    }

    uart_print("[NET] unknown command\n");
    net_send_response("ERR", 3);
}

void poll_network(void) {
    /* Read available bytes into buffer */
    while (net_available() && net_buf_len < NET_BUF_SIZE) {
        net_buf[net_buf_len++] = net_getc();
    }

    /* Check for complete packet: "POKE" + len(4) + payload */
    while (net_buf_len >= 8) {
        if (net_buf[0]=='P' && net_buf[1]=='O' && net_buf[2]=='K' && net_buf[3]=='E') {
            u32 plen = net_buf[4] | (net_buf[5]<<8) | (net_buf[6]<<16) | (net_buf[7]<<24);
            if (plen > NET_BUF_SIZE - 8) { net_buf_len = 0; break; } /* too big */
            if (net_buf_len >= (int)(8 + plen)) {
                /* Complete packet */
                handle_net_packet(net_buf + 8, plen);
                /* Shift buffer */
                int consumed = 8 + plen;
                int left = net_buf_len - consumed;
                if (left > 0) {
                    for (int i = 0; i < left; i++)
                        net_buf[i] = net_buf[consumed + i];
                }
                net_buf_len = left;
            } else {
                break; /* need more data */
            }
        } else {
            /* Sync lost — skip one byte */
            for (int i = 0; i < net_buf_len - 1; i++)
                net_buf[i] = net_buf[i + 1];
            net_buf_len--;
        }
    }
}

/* ── Audio (simple square wave via UART2 or PWM) ── */
/*
 * QEMU virt doesn't have a real audio device easily.
 * We simulate audio by generating PCM data and sending
 * it out via a 3rd serial port mapped to a pipe/file.
 * The host can play it with aplay/sox.
 *
 * For now: generate tone data and write to UART2.
 * QEMU: -serial ... -serial ... -serial file:/tmp/audio.pcm
 */

#define UART2_BASE 0x09050000
#define UART2_DR   (*(volatile u32 *)(UART2_BASE + 0x00))
#define UART2_FR   (*(volatile u32 *)(UART2_BASE + 0x18))

void audio_putc(u8 val) {
    while (UART2_FR & (1 << 5));
    UART2_DR = val;
}

void play_tone(u16 freq, u16 duration_ms) {
    /* Generate 8-bit unsigned PCM, 8000 Hz sample rate */
    u32 samples = (8000 * duration_ms) / 1000;
    u32 period = 8000 / (freq ? freq : 440);
    u32 half = period / 2;

    for (u32 i = 0; i < samples; i++) {
        u8 val = ((i % period) < half) ? 200 : 56; /* square wave */
        audio_putc(val);
    }
    uart_print("[AUDIO] done\n");
}

/* ── Shell ── */
#define CMD_BUF_SIZE 256
static char cmd_buf[CMD_BUF_SIZE];
static int cmd_len = 0;

void shell_prompt(void) {
    uart_print("poke-arm> ");
}

void shell_exec(void) {
    cmd_buf[cmd_len] = 0;

    if (str_eq(cmd_buf, "help")) {
        uart_print("\ncommands: help, info, test, tone, net\n");
    } else if (str_eq(cmd_buf, "info")) {
        uart_print("\nPOKE OS ARM64 v0.2\n");
        uart_print("arch: aarch64 | platform: QEMU virt\n");
        uart_print("UART0: console | UART1: network | UART2: audio\n");
    } else if (str_eq(cmd_buf, "test")) {
        uart_print("\n2+2=");
        /* ARM64: mov x0, #2; add x0, x0, #2; ret */
        code_buf[0]=0x40; code_buf[1]=0x00; code_buf[2]=0x80; code_buf[3]=0xD2;
        code_buf[4]=0x00; code_buf[5]=0x08; code_buf[6]=0x00; code_buf[7]=0x91;
        code_buf[8]=0xC0; code_buf[9]=0x03; code_buf[10]=0x5F; code_buf[11]=0xD6;
        u64 (*fn)(void) = (u64 (*)(void))code_buf;
        u64 r = fn();
        uart_dec(r);
        uart_print("\n");
    } else if (str_eq(cmd_buf, "tone")) {
        uart_print("\nPlaying 440Hz 500ms...\n");
        play_tone(440, 500);
    } else if (str_eq(cmd_buf, "net")) {
        uart_print("\nSending PING on UART1...\n");
        net_print("HELLO FROM POKE ARM\n");
    } else if (cmd_len > 0) {
        uart_print("\nunknown: ");
        uart_print(cmd_buf);
        uart_print("\n");
    }

    cmd_len = 0;
    shell_prompt();
}

/* ── Kernel Main ── */
void kernel_main(void) {
    uart_print("\n");
    uart_print("  ____   ___  _  _______ \n");
    uart_print(" |  _ \\ / _ \\| |/ / ____|\n");
    uart_print(" | |_) | | | | ' /|  _|  \n");
    uart_print(" |  __/| |_| | . \\| |___ \n");
    uart_print(" |_|    \\___/|_|\\_\\_____|\n");
    uart_print("\n");
    uart_print("  POKE OS ARM64 v0.2\n");
    uart_print("  arch: aarch64\n");
    uart_print("  UART0: console\n");
    uart_print("  UART1: network (serial↔TCP)\n");
    uart_print("  UART2: audio out\n");
    uart_print("\n");

    net_init();
    uart_print("[NET] UART1 initialized\n");

    shell_prompt();

    while (1) {
        /* Poll console */
        char c = uart_getc();
        if (c) {
            if (c == '\r' || c == '\n') {
                uart_print("\n");
                shell_exec();
            } else if (c == 127 || c == '\b') {
                if (cmd_len > 0) { cmd_len--; uart_print("\b \b"); }
            } else if (cmd_len < CMD_BUF_SIZE - 1) {
                cmd_buf[cmd_len++] = c;
                uart_putc(c);
            }
        }

        /* Poll network */
        poll_network();
    }
}
