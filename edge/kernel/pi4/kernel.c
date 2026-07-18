/* ============================================
 * POKE OS — Raspberry Pi 4 (BCM2711) Kernel
 *
 * "The future doesn't need an operating system."
 * Code is volatile — generated, executed, discarded.
 *
 * Bare-metal ARM64 kernel with:
 *   - PL011 UART (serial console + POKE protocol)
 *   - Code injection (ARM64 machine code)
 *   - GPIO (virtual for now, real GPIO at 0xFE200000)
 *   - Virtual temperature sensor
 *
 * Target: Raspberry Pi 4 Model B (BCM2711, Cortex-A72)
 * ============================================ */

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long u64;

/* ── BCM2711 Peripheral Base: 0xFE000000 ── */
#define PERI_BASE   0xFE000000

/* PL011 UART0 */
#define UART0_BASE  (PERI_BASE + 0x201000)
#define UART0_DR    (*(volatile u32 *)(UART0_BASE + 0x00))
#define UART0_FR    (*(volatile u32 *)(UART0_BASE + 0x18))
#define UART0_IBRD  (*(volatile u32 *)(UART0_BASE + 0x24))
#define UART0_FBRD  (*(volatile u32 *)(UART0_BASE + 0x28))
#define UART0_LCR   (*(volatile u32 *)(UART0_BASE + 0x2C))
#define UART0_CR    (*(volatile u32 *)(UART0_BASE + 0x30))
#define UART0_ICR   (*(volatile u32 *)(UART0_BASE + 0x44))

/* GPIO */
#define GPIO_BASE   (PERI_BASE + 0x200000)
#define GPFSEL1     (*(volatile u32 *)(GPIO_BASE + 0x04))
#define GPPUD       (*(volatile u32 *)(GPIO_BASE + 0x94))
#define GPPUDCLK0   (*(volatile u32 *)(GPIO_BASE + 0x98))
#define GPIO_PUP_PDN_CNTRL_REG0 (*(volatile u32 *)(GPIO_BASE + 0xE4))

/* ── UART init ── */
static void uart_init(void) {
    /* Disable UART */
    UART0_CR = 0;

    /* Setup GPIO 14,15 for UART (Alt0) */
    u32 sel = GPFSEL1;
    sel &= ~(7 << 12);  /* GPIO14 */
    sel |= (4 << 12);   /* Alt0 */
    sel &= ~(7 << 15);  /* GPIO15 */
    sel |= (4 << 15);   /* Alt0 */
    GPFSEL1 = sel;

    /* Disable pull-up/down for GPIO 14,15 (Pi4 style) */
    GPIO_PUP_PDN_CNTRL_REG0 &= ~(0xF << 28);  /* GPIO14,15 = no pull */

    /* Clear interrupts */
    UART0_ICR = 0x7FF;

    /* Baud rate: 115200 @ 48MHz UART clock
     * Divider = 48000000 / (16 * 115200) = 26.041...
     * IBRD = 26, FBRD = round(0.041 * 64) = 3 */
    UART0_IBRD = 26;
    UART0_FBRD = 3;

    /* 8N1, FIFO enable */
    UART0_LCR = (3 << 5) | (1 << 4);

    /* Enable UART, TX, RX */
    UART0_CR = (1 << 0) | (1 << 8) | (1 << 9);
}

/* ── UART I/O ── */
static void uart_putc(char c) {
    while (UART0_FR & (1 << 5)) {}
    UART0_DR = c;
}

static void uart_print(const char *s) {
    while (*s) {
        if (*s == '\n') uart_putc('\r');
        uart_putc(*s++);
    }
}

static int uart_available(void) {
    return !(UART0_FR & (1 << 4));
}

static u8 uart_getc(void) {
    return UART0_DR & 0xFF;
}

static void uart_hex32(u32 val) {
    const char hex[] = "0123456789abcdef";
    uart_print("0x");
    for (int i = 28; i >= 0; i -= 4)
        uart_putc(hex[(val >> i) & 0xF]);
}

static int itoa_dec(u32 val, char *buf) {
    char tmp[12];
    int i = 0;
    if (val == 0) { buf[0] = '0'; return 1; }
    while (val > 0) { tmp[i++] = '0' + (val % 10); val /= 10; }
    int len = i;
    for (int j = 0; j < len; j++) buf[j] = tmp[len - 1 - j];
    return len;
}

static void uart_dec(u32 val) {
    char buf[12];
    int len = itoa_dec(val, buf);
    for (int i = 0; i < len; i++) uart_putc(buf[i]);
}

/* ── Memory utils ── */
static void mem_copy(void *dst, const void *src, int n) {
    u8 *d = dst; const u8 *s = src;
    while (n-- > 0) *d++ = *s++;
}

static void mem_set(void *dst, u8 val, int n) {
    u8 *d = dst;
    while (n-- > 0) *d++ = val;
}

static int mem_cmp(const void *a, const void *b, int n) {
    const u8 *p = a, *q = b;
    while (n-- > 0) { if (*p != *q) return *p - *q; p++; q++; }
    return 0;
}

static int str_len(const char *s) {
    int n = 0; while (*s++) n++; return n;
}

static int str_copy(char *dst, const char *src) {
    int n = 0;
    while (*src) { dst[n++] = *src++; }
    return n;
}

/* ── Code Execution Buffer ── */
#define CODE_BUF_SIZE 4096
static u8 code_buf[CODE_BUF_SIZE] __attribute__((aligned(4096)));
static char result_buf[256];
static int result_len = 0;

/* ── Virtual GPIO ── */
#define VGPIO_COUNT 11
static u8 vgpio_pins[VGPIO_COUNT];

/* ── POKE Protocol ── */
#define RX_BUF_SIZE 4096
static u8 rx_buf[RX_BUF_SIZE];
static int rx_pos = 0;

static void send_frame(const char *magic, const u8 *payload, u32 len) {
    for (int i = 0; i < 4; i++) uart_putc(magic[i]);
    uart_putc(len & 0xFF);
    uart_putc((len >> 8) & 0xFF);
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

/* ── Command Handlers ── */

static void handle_exec(const u8 *code, int code_len) {
    if (code_len <= 0 || code_len > CODE_BUF_SIZE) {
        send_resp_str("error: invalid code size");
        return;
    }
    mem_copy(code_buf, code, code_len);

    /* Check for RET (0xD65F03C0) */
    int has_ret = 0;
    for (int i = 0; i <= code_len - 4; i += 4) {
        u32 insn = code_buf[i] | (code_buf[i+1]<<8) | (code_buf[i+2]<<16) | (code_buf[i+3]<<24);
        if (insn == 0xD65F03C0) { has_ret = 1; break; }
    }

    mem_set(result_buf, 0, 256);
    result_len = 0;
    u64 ret_x0 = 0;

    if (has_ret) {
        /* Flush instruction cache */
        __asm__ volatile("dc civac, %0" :: "r"(code_buf));
        __asm__ volatile("dsb sy");
        __asm__ volatile("ic ivau, %0" :: "r"(code_buf));
        __asm__ volatile("dsb sy");
        __asm__ volatile("isb");

        u64 (*fn)(char *, int *) = (u64 (*)(char *, int *))code_buf;
        ret_x0 = fn(result_buf, &result_len);
    }

    char resp[128];
    int rlen = 0;
    if (!has_ret) {
        rlen = str_copy(resp, "no RET found, not executed");
    } else if (result_len > 0) {
        mem_copy(resp, result_buf, result_len);
        rlen = result_len;
    } else {
        rlen += str_copy(resp + rlen, "x0=");
        rlen += itoa_dec((u32)ret_x0, resp + rlen);
    }
    send_resp((const u8 *)resp, rlen);
}

static void handle_info(void) {
    char resp[384];
    int n = 0;
    n += str_copy(resp + n, "{\"status\":\"alive\",\"arch\":\"aarch64\",\"chip\":\"bcm2711\"");
    n += str_copy(resp + n, ",\"kernel\":\"poke-os\",\"transport\":\"uart\"");
    n += str_copy(resp + n, ",\"commands\":[\"PING\",\"INFO\",\"EXEC\",\"GPIO\",\"GPOS\",\"TEMP\"]");
    n += str_copy(resp + n, ",\"bare_metal\":true}");
    resp[n] = 0;
    send_resp_str(resp);
}

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

static void handle_gpos(const u8 *data, int data_len) {
    if (data_len < 2) { send_resp_str("{\"error\":\"need pin+value\"}"); return; }
    u8 pin = data[0];
    u8 val = data[1];
    if (pin >= VGPIO_COUNT) { send_resp_str("{\"error\":\"invalid pin\"}"); return; }
    vgpio_pins[pin] = val ? 1 : 0;
    char resp[64];
    int n = 0;
    n += str_copy(resp + n, "{\"pin\":");
    n += itoa_dec(pin, resp + n);
    n += str_copy(resp + n, ",\"value\":");
    resp[n++] = '0' + (val ? 1 : 0);
    resp[n++] = '}';
    resp[n] = 0;
    send_resp_str(resp);
}

static void handle_temp(void) {
    static u32 tick = 0;
    tick++;
    u32 temp_x10 = 420 + (tick % 30);  /* 42.0 ~ 44.9 °C (Pi4 runs hot) */
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

/* ── Frame processing ── */
static void process_frame(const u8 *payload, u32 payload_len) {
    if (payload_len < 4) { send_resp_str("error: payload too short"); return; }

    if (mem_cmp(payload, "PING", 4) == 0) {
        send_resp_str("PONG");
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
    } else {
        send_resp_str("error: unknown command");
    }
}

/* ── Poll UART for POKE frames ── */
static void poll_protocol(void) {
    while (rx_pos >= 8) {
        if (rx_buf[0] == 'P' && rx_buf[1] == 'O' && rx_buf[2] == 'K' && rx_buf[3] == 'E') {
            u32 plen = rx_buf[4] | (rx_buf[5] << 8) | (rx_buf[6] << 16) | (rx_buf[7] << 24);
            if (plen > RX_BUF_SIZE - 8) { rx_pos = 0; break; }
            if (rx_pos >= (int)(8 + plen)) {
                process_frame(rx_buf + 8, plen);
                int consumed = 8 + plen;
                int left = rx_pos - consumed;
                if (left > 0) {
                    for (int i = 0; i < left; i++) rx_buf[i] = rx_buf[consumed + i];
                }
                rx_pos = left;
            } else break;
        } else {
            for (int i = 0; i < rx_pos - 1; i++) rx_buf[i] = rx_buf[i + 1];
            rx_pos--;
        }
    }
}

/* ── Kernel Main ── */
void kernel_main(void) {
    uart_init();

    uart_print("\n");
    uart_print("  ____   ___  _  _______ \n");
    uart_print(" |  _ \\ / _ \\| |/ / ____|\n");
    uart_print(" | |_) | | | | ' /|  _|  \n");
    uart_print(" |  __/| |_| | . \\| |___ \n");
    uart_print(" |_|    \\___/|_|\\_\\_____|\n");
    uart_print("\n");
    uart_print("  POKE OS Raspberry Pi 4 v0.1 (bare-metal)\n");
    uart_print("  arch: aarch64 (Cortex-A72)\n");
    uart_print("  kernel: poke-os\n");
    uart_print("  transport: UART0 @ 0xFE201000\n");
    uart_print("  code_buf: ");
    uart_hex32((u32)(u64)code_buf);
    uart_print("\n\n");

    uart_print("poke-pi4> ");

    /* Main loop */
    while (1) {
        while (uart_available() && rx_pos < RX_BUF_SIZE) {
            u8 b = uart_getc();
            rx_buf[rx_pos++] = b;
        }

        poll_protocol();

        /* Shell: drain non-POKE bytes */
        while (rx_pos > 0) {
            static const char magic[] = "POKE";
            int could_be_poke = 1;
            int check_len = rx_pos < 4 ? rx_pos : 4;
            for (int i = 0; i < check_len; i++) {
                if (rx_buf[i] != magic[i]) { could_be_poke = 0; break; }
            }
            if (could_be_poke) break;

            char c = (char)rx_buf[0];
            for (int i = 0; i < rx_pos - 1; i++) rx_buf[i] = rx_buf[i + 1];
            rx_pos--;

            if (c == '\r' || c == '\n') {
                uart_print("\npoke-pi4> ");
            }
        }
    }
}
