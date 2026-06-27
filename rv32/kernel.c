/* ============================================
 * POKE OS RV32 — Kernel
 * UART (console + POKE protocol) + Code Execution
 * Target: QEMU virt machine (riscv32)
 *
 * "The future doesn't need an operating system."
 * Code is volatile — generated, executed, discarded.
 * ============================================ */

typedef unsigned char      u8;
typedef unsigned short     u16;
typedef unsigned int       u32;

/* ── NS16550 UART (QEMU virt: 0x10000000) ── */
#define UART_BASE   0x10000000
#define UART_THR    (*(volatile u8 *)(UART_BASE + 0x00))  /* TX holding */
#define UART_RBR    (*(volatile u8 *)(UART_BASE + 0x00))  /* RX buffer  */
#define UART_IER    (*(volatile u8 *)(UART_BASE + 0x01))  /* int enable */
#define UART_FCR    (*(volatile u8 *)(UART_BASE + 0x02))  /* FIFO ctrl  */
#define UART_LCR    (*(volatile u8 *)(UART_BASE + 0x03))  /* line ctrl  */
#define UART_LSR    (*(volatile u8 *)(UART_BASE + 0x05))  /* line status*/

#define LSR_RX_READY  0x01
#define LSR_TX_EMPTY   0x20

/* ── UART driver ── */
static void uart_init(void) {
    UART_IER = 0x00;        /* disable interrupts */
    UART_LCR = 0x80;        /* DLAB on */
    /* divisor = 1 (QEMU doesn't care about baud) */
    (*(volatile u8 *)(UART_BASE + 0x00)) = 0x01;
    (*(volatile u8 *)(UART_BASE + 0x01)) = 0x00;
    UART_LCR = 0x03;        /* 8N1, DLAB off */
    UART_FCR = 0x07;        /* enable + clear FIFOs */
}

static void uart_putc(char c) {
    while (!(UART_LSR & LSR_TX_EMPTY));
    UART_THR = c;
}

static void uart_print(const char *s) {
    while (*s) {
        if (*s == '\n') uart_putc('\r');
        uart_putc(*s++);
    }
}

static void uart_hex32(u32 val) {
    const char hex[] = "0123456789ABCDEF";
    uart_print("0x");
    for (int i = 28; i >= 0; i -= 4)
        uart_putc(hex[(val >> i) & 0xF]);
}

static void uart_dec(u32 val) {
    char buf[12];
    int i = 0;
    if (val == 0) { uart_putc('0'); return; }
    while (val > 0) { buf[i++] = '0' + (val % 10); val /= 10; }
    while (i > 0) uart_putc(buf[--i]);
}

static char uart_getc(void) {
    if (!(UART_LSR & LSR_RX_READY)) return 0;
    return UART_RBR;
}

static int uart_available(void) {
    return (UART_LSR & LSR_RX_READY);
}

/* ── Memory / String utils (no libc) ── */
static void mem_copy(void *dst, const void *src, int n) {
    u8 *d = (u8 *)dst;
    const u8 *s = (const u8 *)src;
    while (n-- > 0) *d++ = *s++;
}

static void mem_set(void *dst, u8 val, int n) {
    u8 *d = (u8 *)dst;
    while (n-- > 0) *d++ = val;
}

static int str_eq(const char *a, const char *b) {
    while (*a && *b) { if (*a++ != *b++) return 0; }
    return *a == *b;
}

static int str_len(const char *s) {
    int n = 0; while (*s++) n++; return n;
}

static int mem_cmp(const void *a, const void *b, int n) {
    const u8 *pa = (const u8 *)a;
    const u8 *pb = (const u8 *)b;
    for (int i = 0; i < n; i++) {
        if (pa[i] != pb[i]) return pa[i] - pb[i];
    }
    return 0;
}

/* int to decimal string, returns length */
static int itoa_dec(u32 val, char *buf) {
    char tmp[12];
    int i = 0, len = 0;
    if (val == 0) { buf[0] = '0'; return 1; }
    while (val > 0) { tmp[i++] = '0' + (val % 10); val /= 10; }
    while (i > 0) buf[len++] = tmp[--i];
    return len;
}

/* simple string copy, returns bytes written */
static int str_copy(char *dst, const char *src) {
    int n = 0;
    while (*src) { *dst++ = *src++; n++; }
    return n;
}

/* ── Tick counter (mtime @ 0x0200BFF8 for QEMU virt CLINT) ── */
#define MTIME_LO  (*(volatile u32 *)0x0200BFF8)
#define MTIME_HI  (*(volatile u32 *)0x0200BFFC)

static u32 get_ticks(void) {
    return MTIME_LO;
}

/* QEMU virt CLINT runs at 10MHz */
#define TICKS_PER_SEC  10000000U

/* ── Code Execution ── */
#define CODE_BUF_SIZE  4096
static u8 code_buf[CODE_BUF_SIZE] __attribute__((aligned(4096)));

#define RESULT_BUF_SIZE 256
static char result_buf[RESULT_BUF_SIZE];
static int  result_len = 0;

/* ── Virtual GPIO (11 pins, in RAM) ── */
#define VGPIO_COUNT 11
static u8 vgpio_pins[VGPIO_COUNT];   /* 0 or 1 */
static u8 vgpio_dir[VGPIO_COUNT];    /* 0=input, 1=output */

/* ── Virtual Temperature (timer-based pseudo) ── */
static u32 vtemp_get(void) {
    /* Returns pseudo temperature in 0.1C units based on tick counter */
    u32 t = get_ticks();
    /* Simulate ~25.0 C with small variation from tick LSBs */
    return 250 + (t & 0x1F);  /* 25.0 ~ 28.1 C */
}

/* ── Event monitors ── */
typedef struct {
    int  gpio_enabled;
    u8   gpio_pin;
    u8   gpio_last_val;
    int  temp_enabled;
    u32  temp_threshold_x10; /* in 0.1C */
    u8   temp_op;            /* 0=above, 1=below */
    u32  temp_interval_ticks;
    u32  temp_last_check;
    int  temp_fired;
} event_monitor_t;

static event_monitor_t monitor;

/* ── POKE Serial Protocol ── */
/*
 * Request:  "POKE" (4) + payload_len (4 LE) + payload
 * Response: "RESP" (4) + payload_len (4 LE) + payload
 * Event:    "EVNT" (4) + payload_len (4 LE) + JSON payload
 */

#define RX_BUF_SIZE 4096
static u8  rx_buf[RX_BUF_SIZE];
static int rx_pos = 0;

static void send_frame(const char *magic, const u8 *payload, u32 len) {
    for (int i = 0; i < 4; i++) uart_putc(magic[i]);
    uart_putc(len & 0xFF);
    uart_putc((len >>  8) & 0xFF);
    uart_putc((len >> 16) & 0xFF);
    uart_putc((len >> 24) & 0xFF);
    for (u32 i = 0; i < len; i++) uart_putc(payload[i]);
}

static void send_resp(const u8 *payload, u32 len) {
    send_frame("RESP", payload, len);
}

static void send_resp_str(const char *s) {
    send_resp((const u8 *)s, str_len(s));
}

static void send_event(const char *json) {
    send_frame("EVNT", (const u8 *)json, str_len(json));
}

/* ── Command: PING ── */
static void handle_ping(void) {
    send_resp_str("PONG");
}

/* ── Command: INFO ── */
static void handle_info(void) {
    char resp[256];
    int n = 0;
    n += str_copy(resp + n, "{\"status\":\"alive\",\"arch\":\"rv32im\",\"platform\":\"qemu-virt\",");
    n += str_copy(resp + n, "\"commands\":[\"PING\",\"INFO\",\"EXEC\",\"GPIO\",\"GPOS\",\"TEMP\",\"EMON\",\"ESTOP\"],");
    n += str_copy(resp + n, "\"gpio_pins\":11,\"uptime\":");
    n += itoa_dec(get_ticks() / TICKS_PER_SEC, resp + n);
    n += str_copy(resp + n, "}");
    resp[n] = 0;
    send_resp_str(resp);
}

/* ── Command: EXEC ── */
static void handle_exec(const u8 *code, int code_len) {
    if (code_len <= 0 || code_len > CODE_BUF_SIZE) {
        send_resp_str("error: invalid code size");
        return;
    }

    mem_copy(code_buf, code, code_len);

    uart_print("[EXEC] ");
    uart_dec(code_len);
    uart_print(" bytes\n");

    /* Sync instruction cache */
    __asm__ volatile("fence.i");

    mem_set(result_buf, 0, RESULT_BUF_SIZE);
    result_len = 0;

    /* Execute: a0 = result_buf, a1 = &result_len, return value in a0 */
    u32 (*fn)(char *, int *) = (u32 (*)(char *, int *))code_buf;
    u32 ret = fn(result_buf, &result_len);

    uart_print("[EXEC] a0=");
    uart_dec(ret);
    uart_print("\n");

    /* Build response */
    char resp[320];
    int rlen = 0;

    if (result_len > 0 && result_len < RESULT_BUF_SIZE) {
        mem_copy(resp, result_buf, result_len);
        rlen = result_len;
    } else {
        rlen += str_copy(resp + rlen, "a0=");
        rlen += itoa_dec(ret, resp + rlen);
    }

    send_resp((const u8 *)resp, rlen);
}

/* ── Command: GPIO (read all pins) ── */
static void handle_gpio(void) {
    char resp[256];
    int n = 0;
    n += str_copy(resp + n, "{\"gpio\":{");
    for (int pin = 0; pin < VGPIO_COUNT; pin++) {
        if (pin > 0) resp[n++] = ',';
        resp[n++] = '"';
        n += itoa_dec(pin, resp + n);
        resp[n++] = '"';
        resp[n++] = ':';
        resp[n++] = '0' + (vgpio_pins[pin] ? 1 : 0);
    }
    n += str_copy(resp + n, "}}");
    resp[n] = 0;
    send_resp_str(resp);
}

/* ── Command: GPOS (set GPIO pin) ── */
static void handle_gpos(const u8 *data, int data_len) {
    if (data_len < 2) {
        send_resp_str("{\"error\":\"need pin + value\"}");
        return;
    }
    u8 pin = data[0];
    u8 val = data[1];
    if (pin >= VGPIO_COUNT) {
        send_resp_str("{\"error\":\"invalid pin\"}");
        return;
    }
    vgpio_dir[pin] = 1;  /* set as output */
    vgpio_pins[pin] = val ? 1 : 0;

    char resp[64];
    int n = 0;
    n += str_copy(resp + n, "{\"pin\":");
    n += itoa_dec(pin, resp + n);
    n += str_copy(resp + n, ",\"value\":");
    resp[n++] = '0' + (val ? 1 : 0);
    n += str_copy(resp + n, "}");
    resp[n] = 0;
    send_resp_str(resp);
}

/* ── Command: TEMP ── */
static void handle_temp(void) {
    u32 temp_x10 = vtemp_get();
    char resp[64];
    int n = 0;
    n += str_copy(resp + n, "{\"celsius\":");
    n += itoa_dec(temp_x10 / 10, resp + n);
    resp[n++] = '.';
    resp[n++] = '0' + (temp_x10 % 10);
    n += str_copy(resp + n, ",\"virtual\":true}");
    resp[n] = 0;
    send_resp_str(resp);
}

/* ── Command: EMON (event monitor) ── */
/*
 * type=0x01: GPIO watch — pin(1) + edge(1: 0=falling,1=rising,2=any)
 * type=0x02: Temp watch — op(1: 0=above,1=below) + threshold_x10(2 LE) + interval_ms(2 LE)
 */
static void handle_emon(const u8 *data, int data_len) {
    if (data_len < 1) {
        send_resp_str("{\"error\":\"empty config\"}");
        return;
    }

    u8 type = data[0];

    if (type == 0x01 && data_len >= 3) {
        u8 pin = data[1];
        if (pin >= VGPIO_COUNT) {
            send_resp_str("{\"error\":\"invalid pin\"}");
            return;
        }
        monitor.gpio_enabled = 1;
        monitor.gpio_pin = pin;
        monitor.gpio_last_val = vgpio_pins[pin];

        char resp[96];
        int n = 0;
        n += str_copy(resp + n, "{\"ok\":true,\"monitor\":\"gpio\",\"pin\":");
        n += itoa_dec(pin, resp + n);
        n += str_copy(resp + n, "}");
        resp[n] = 0;
        send_resp_str(resp);

    } else if (type == 0x02 && data_len >= 6) {
        monitor.temp_op = data[1];
        monitor.temp_threshold_x10 = data[2] | (data[3] << 8);
        u32 interval_ms = data[4] | (data[5] << 8);
        if (interval_ms < 1000) interval_ms = 1000;
        monitor.temp_interval_ticks = (TICKS_PER_SEC / 1000) * interval_ms;
        monitor.temp_enabled = 1;
        monitor.temp_fired = 0;
        monitor.temp_last_check = get_ticks();

        char resp[128];
        int n = 0;
        n += str_copy(resp + n, "{\"ok\":true,\"monitor\":\"temp\",\"op\":\"");
        n += str_copy(resp + n, monitor.temp_op == 0 ? "above" : "below");
        n += str_copy(resp + n, "\",\"threshold\":");
        n += itoa_dec(monitor.temp_threshold_x10 / 10, resp + n);
        resp[n++] = '.';
        resp[n++] = '0' + (monitor.temp_threshold_x10 % 10);
        n += str_copy(resp + n, "}");
        resp[n] = 0;
        send_resp_str(resp);

    } else {
        send_resp_str("{\"error\":\"unknown monitor type or bad length\"}");
    }
}

/* ── Command: ESTOP ── */
static void handle_estop(void) {
    monitor.gpio_enabled = 0;
    monitor.temp_enabled = 0;
    monitor.temp_fired = 0;
    send_resp_str("{\"ok\":true,\"stopped\":\"all\"}");
}

/* ── Process a complete POKE frame ── */
static void process_frame(const u8 *payload, u32 payload_len) {
    if (payload_len < 4) {
        send_resp_str("error: payload too short");
        return;
    }

    if (mem_cmp(payload, "PING", 4) == 0) {
        handle_ping();
    } else if (mem_cmp(payload, "INFO", 4) == 0) {
        handle_info();
    } else if (mem_cmp(payload, "EXEC", 4) == 0) {
        handle_exec(payload + 4, payload_len - 4);
    } else if (mem_cmp(payload, "GPIO", 4) == 0) {
        handle_gpio();
    } else if (mem_cmp(payload, "GPOS", 4) == 0) {
        handle_gpos(payload + 4, payload_len - 4);
    } else if (mem_cmp(payload, "TEMP", 4) == 0) {
        handle_temp();
    } else if (mem_cmp(payload, "EMON", 4) == 0) {
        handle_emon(payload + 4, payload_len - 4);
    } else if (mem_cmp(payload, "ESTO", 4) == 0) {
        handle_estop();
    } else {
        send_resp_str("error: unknown command");
    }
}

/* ── Poll for POKE frames (bytes already in rx_buf) ── */
static void poll_protocol(void) {
    /* Parse frames: "POKE" + len(4 LE) + payload */
    while (rx_pos >= 8) {
        if (rx_buf[0] == 'P' && rx_buf[1] == 'O' && rx_buf[2] == 'K' && rx_buf[3] == 'E') {
            u32 plen = rx_buf[4] | (rx_buf[5] << 8) | (rx_buf[6] << 16) | (rx_buf[7] << 24);
            if (plen > RX_BUF_SIZE - 8) {
                /* Too big, discard header */
                rx_pos = 0;
                break;
            }
            if (rx_pos >= (int)(8 + plen)) {
                /* Complete packet */
                process_frame(rx_buf + 8, plen);
                /* Shift buffer */
                int consumed = 8 + plen;
                int left = rx_pos - consumed;
                if (left > 0) {
                    for (int i = 0; i < left; i++)
                        rx_buf[i] = rx_buf[consumed + i];
                }
                rx_pos = left;
            } else {
                break;  /* need more data */
            }
        } else {
            /* Sync lost, skip one byte */
            for (int i = 0; i < rx_pos - 1; i++)
                rx_buf[i] = rx_buf[i + 1];
            rx_pos--;
        }
    }
}

/* ── Check event monitors ── */
static void check_monitors(void) {
    /* GPIO watch: detect pin value change */
    if (monitor.gpio_enabled) {
        u8 cur = vgpio_pins[monitor.gpio_pin];
        if (cur != monitor.gpio_last_val) {
            char evt[128];
            int n = 0;
            n += str_copy(evt + n, "{\"type\":\"gpio\",\"pin\":");
            n += itoa_dec(monitor.gpio_pin, evt + n);
            n += str_copy(evt + n, ",\"value\":");
            evt[n++] = '0' + cur;
            n += str_copy(evt + n, ",\"edge\":\"");
            n += str_copy(evt + n, cur ? "rising" : "falling");
            n += str_copy(evt + n, "\"}");
            evt[n] = 0;
            send_event(evt);
            monitor.gpio_last_val = cur;
        }
    }

    /* Temperature threshold */
    if (monitor.temp_enabled) {
        u32 now = get_ticks();
        if ((now - monitor.temp_last_check) >= monitor.temp_interval_ticks) {
            monitor.temp_last_check = now;
            u32 temp_x10 = vtemp_get();
            int triggered = 0;
            if (monitor.temp_op == 0 && temp_x10 > monitor.temp_threshold_x10) triggered = 1;
            if (monitor.temp_op == 1 && temp_x10 < monitor.temp_threshold_x10) triggered = 1;

            if (triggered && !monitor.temp_fired) {
                monitor.temp_fired = 1;
                char evt[128];
                int n = 0;
                n += str_copy(evt + n, "{\"type\":\"temp\",\"celsius\":");
                n += itoa_dec(temp_x10 / 10, evt + n);
                evt[n++] = '.';
                evt[n++] = '0' + (temp_x10 % 10);
                n += str_copy(evt + n, ",\"threshold\":");
                n += itoa_dec(monitor.temp_threshold_x10 / 10, evt + n);
                evt[n++] = '.';
                evt[n++] = '0' + (monitor.temp_threshold_x10 % 10);
                n += str_copy(evt + n, ",\"op\":\"");
                n += str_copy(evt + n, monitor.temp_op == 0 ? "above" : "below");
                n += str_copy(evt + n, "\"}");
                evt[n] = 0;
                send_event(evt);
            } else if (!triggered) {
                monitor.temp_fired = 0;  /* reset when condition clears */
            }
        }
    }
}

/* ── Shell ── */
#define CMD_BUF_SIZE 256
static char cmd_buf[CMD_BUF_SIZE];
static int  cmd_len = 0;

static void shell_prompt(void) {
    uart_print("poke-rv32> ");
}

static void shell_exec(void) {
    cmd_buf[cmd_len] = 0;

    if (str_eq(cmd_buf, "help")) {
        uart_print("\ncommands: help, info, test, gpio, temp\n");

    } else if (str_eq(cmd_buf, "info")) {
        uart_print("\nPOKE OS RV32 v0.1\n");
        uart_print("arch: rv32im | platform: QEMU virt\n");
        uart_print("UART: NS16550 @ 0x10000000\n");
        uart_print("code_buf: ");
        uart_hex32((u32)(unsigned long)code_buf);
        uart_print(" (");
        uart_dec(CODE_BUF_SIZE);
        uart_print(" bytes)\n");
        uart_print("uptime: ");
        uart_dec(get_ticks() / TICKS_PER_SEC);
        uart_print("s\n");

    } else if (str_eq(cmd_buf, "test")) {
        uart_print("\n2+2=");
        /*
         * RV32I machine code:
         *   li a0, 2        → 0x00200513  (addi a0, zero, 2)
         *   addi a0, a0, 2  → 0x00250513
         *   ret             → 0x00008067  (jalr zero, ra, 0)
         */
        code_buf[0]=0x13; code_buf[1]=0x05; code_buf[2]=0x20; code_buf[3]=0x00;
        code_buf[4]=0x13; code_buf[5]=0x05; code_buf[6]=0x25; code_buf[7]=0x00;
        code_buf[8]=0x67; code_buf[9]=0x80; code_buf[10]=0x00; code_buf[11]=0x00;
        __asm__ volatile("fence.i");
        u32 (*fn)(void) = (u32 (*)(void))code_buf;
        u32 r = fn();
        uart_dec(r);
        uart_print("\n");

    } else if (str_eq(cmd_buf, "gpio")) {
        uart_print("\nVirtual GPIO pins:\n");
        for (int i = 0; i < VGPIO_COUNT; i++) {
            uart_print("  GPIO");
            uart_dec(i);
            uart_print(": ");
            uart_putc('0' + vgpio_pins[i]);
            uart_print(vgpio_dir[i] ? " (out)" : " (in)");
            uart_print("\n");
        }

    } else if (str_eq(cmd_buf, "temp")) {
        u32 t = vtemp_get();
        uart_print("\nTemperature: ");
        uart_dec(t / 10);
        uart_putc('.');
        uart_putc('0' + (t % 10));
        uart_print(" C (virtual)\n");

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
    uart_init();

    /* Init virtual GPIO */
    mem_set(vgpio_pins, 0, VGPIO_COUNT);
    mem_set(vgpio_dir, 0, VGPIO_COUNT);

    /* Init event monitor */
    mem_set(&monitor, 0, sizeof(monitor));

    uart_print("\n");
    uart_print("  ____   ___  _  _______ \n");
    uart_print(" |  _ \\ / _ \\| |/ / ____|\n");
    uart_print(" | |_) | | | | ' /|  _|  \n");
    uart_print(" |  __/| |_| | . \\| |___ \n");
    uart_print(" |_|    \\___/|_|\\_\\_____|\n");
    uart_print("\n");
    uart_print("  POKE OS RV32 v0.1\n");
    uart_print("  arch: rv32im\n");
    uart_print("  platform: QEMU virt\n");
    uart_print("  UART: NS16550 @ 0x10000000\n");
    uart_print("  code_buf: ");
    uart_hex32((u32)(unsigned long)code_buf);
    uart_print("\n\n");

    shell_prompt();

    while (1) {
        /* Read all available bytes into rx_buf */
        while (uart_available() && rx_pos < RX_BUF_SIZE) {
            rx_buf[rx_pos++] = UART_RBR;
        }

        /* Try to parse POKE frames */
        poll_protocol();

        /* Drain non-POKE bytes to shell.
         * Keep bytes that could be a partial "POKE" header. */
        while (rx_pos > 0) {
            /* Check if buffer could be a partial POKE header */
            static const char magic[] = "POKE";
            int could_be_poke = 1;
            int check_len = rx_pos < 4 ? rx_pos : 4;
            for (int i = 0; i < check_len; i++) {
                if (rx_buf[i] != magic[i]) { could_be_poke = 0; break; }
            }
            if (could_be_poke) break;  /* partial or full "POKE" — keep waiting */

            /* First byte is not part of POKE header — feed to shell */
            char c = (char)rx_buf[0];
            /* Shift buffer */
            for (int i = 0; i < rx_pos - 1; i++) rx_buf[i] = rx_buf[i+1];
            rx_pos--;

            if (c == '\r' || c == '\n') {
                uart_print("\n");
                shell_exec();
            } else if (c == 127 || c == '\b') {
                if (cmd_len > 0) { cmd_len--; uart_print("\b \b"); }
            } else if (c >= 0x20 && c < 0x7F && cmd_len < CMD_BUF_SIZE - 1) {
                cmd_buf[cmd_len++] = c;
                uart_putc(c);
            }
        }

        /* Check event monitors */
        check_monitors();
    }
}
