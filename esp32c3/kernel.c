/* ============================================
 * POKE OS ESP32-C3 — Bare-Metal Kernel
 *
 * "The future doesn't need an operating system."
 * Code is volatile — generated, executed, discarded.
 *
 * Direct Boot: no ESP-IDF, no FreeRTOS, no bootloader.
 * USB-Serial/JTAG for POKE protocol communication.
 *
 * Target: ESP32-C3 (RISC-V RV32IMC)
 * ============================================ */

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;

/* ── ESP32-C3 Peripheral Registers ── */

/* USB-Serial/JTAG Controller: 0x60043000 */
#define USB_BASE           0x60043000
#define USB_EP1_REG        (*(volatile u32 *)(USB_BASE + 0x00))
#define USB_EP1_CONF_REG   (*(volatile u32 *)(USB_BASE + 0x04))
#define USB_INT_RAW_REG    (*(volatile u32 *)(USB_BASE + 0x08))
#define USB_INT_ST_REG     (*(volatile u32 *)(USB_BASE + 0x0C))
#define USB_INT_CLR_REG    (*(volatile u32 *)(USB_BASE + 0x14))

/* EP1_CONF bits */
#define USB_WR_DONE              (1 << 0)
#define USB_SERIAL_IN_EP_DATA_FREE (1 << 1)

/* INT bits */
#define USB_SERIAL_OUT_RECV_PKT  (1 << 2)

/* Timer Group 0 (watchdog): 0x6001F000 */
#define TIMG0_BASE         0x6001F000
#define TIMG0_WDTCONFIG0   (*(volatile u32 *)(TIMG0_BASE + 0x48))
#define TIMG0_WDTWPROTECT  (*(volatile u32 *)(TIMG0_BASE + 0x64))

/* RTC Watchdog: 0x60008000 */
#define RTC_BASE           0x60008000
#define RTC_WDT_CONFIG0    (*(volatile u32 *)(RTC_BASE + 0x90))
#define RTC_WDT_WPROTECT   (*(volatile u32 *)(RTC_BASE + 0xA8))

/* Super Watchdog: 0x60008000 + 0xB0 */
#define RTC_SWD_CONF       (*(volatile u32 *)(RTC_BASE + 0xB0))
#define RTC_SWD_WPROTECT   (*(volatile u32 *)(RTC_BASE + 0xB4))

/* System registers */
#define SYSTEM_BASE            0x600C0000
#define SYSTEM_PERIP_CLK_EN0   (*(volatile u32 *)(SYSTEM_BASE + 0x10))
#define SYSTEM_PERIP_RST_EN0   (*(volatile u32 *)(SYSTEM_BASE + 0x14))

/* ── USB-Serial/JTAG init ── */
#define USB_CONF0_REG      (*(volatile u32 *)(USB_BASE + 0x18))
#define USB_CONF0_PHY_SEL        (1 << 0)   /* 0=internal PHY */
#define USB_CONF0_USB_PAD_ENABLE (1 << 14)  /* enable USB pads */

static void usb_init(void) {
    /* DON'T reset the USB peripheral — ROM already initialized it.
     * Just ensure clock is enabled and pads are active. */
    SYSTEM_PERIP_CLK_EN0 |= (1 << 14);
    /* DO NOT touch PERIP_RST_EN0 — resetting USB kills the connection */

    /* Ensure USB PHY pads are enabled (should already be from ROM) */
    USB_CONF0_REG |= USB_CONF0_USB_PAD_ENABLE;

    /* Small delay */
    for (volatile int i = 0; i < 50000; i++) {}
}

/* ── Disable PMS (Permission Management System) for code injection ── */
#define PMS_BASE              0x600C1000
#define PMS_OCCUPY_0          (*(volatile u32 *)(PMS_BASE + 0x00))
/* IRAM0 PMS */
#define PMS_IRAM0_0           (*(volatile u32 *)(PMS_BASE + 0x48))
#define PMS_IRAM0_1           (*(volatile u32 *)(PMS_BASE + 0x4C))
/* DRAM0 PMS */
#define PMS_DRAM0_0           (*(volatile u32 *)(PMS_BASE + 0x80))
#define PMS_DRAM0_1           (*(volatile u32 *)(PMS_BASE + 0x84))

static void pms_disable(void) {
    /* Set all IRAM/DRAM regions to full access (RWX) */
    PMS_IRAM0_0 = 0xFFFFFFFF;
    PMS_IRAM0_1 = 0xFFFFFFFF;
    PMS_DRAM0_0 = 0xFFFFFFFF;
    PMS_DRAM0_1 = 0xFFFFFFFF;
}

/* ── Watchdog disable ── */
static void wdt_disable(void) {
    /* Timer Group 0 WDT */
    TIMG0_WDTWPROTECT = 0x50D83AA1;  /* unlock */
    TIMG0_WDTCONFIG0 = 0;            /* disable */

    /* RTC WDT */
    RTC_WDT_WPROTECT = 0x50D83AA1;   /* unlock */
    RTC_WDT_CONFIG0 = 0;             /* disable */

    /* Super WDT */
    RTC_SWD_WPROTECT = 0x8F1D312A;   /* unlock */
    u32 val = RTC_SWD_CONF;
    val |= (1 << 18);  /* SWD_AUTO_FEED_EN */
    RTC_SWD_CONF = val;
}

/* ── USB-Serial/JTAG I/O ── */
static int tx_count = 0;

static void usb_flush(void) {
    USB_EP1_CONF_REG = USB_WR_DONE;
    tx_count = 0;
    /* Wait briefly for USB packet to be sent */
    for (volatile int i = 0; i < 5000; i++) {}
}

static void usb_putc(char c) {
    /* If FIFO full, flush first and wait */
    if (!(USB_EP1_CONF_REG & USB_SERIAL_IN_EP_DATA_FREE)) {
        usb_flush();
        int timeout = 200000;
        while (!(USB_EP1_CONF_REG & USB_SERIAL_IN_EP_DATA_FREE) && --timeout > 0) {}
    }
    USB_EP1_REG = (u32)(u8)c;
}

static int usb_available(void) {
    return USB_INT_ST_REG & USB_SERIAL_OUT_RECV_PKT;
}

static u8 usb_getc(void) {
    u8 c = (u8)(USB_EP1_REG & 0xFF);
    /* Clear interrupt if no more data */
    if (!(USB_INT_RAW_REG & USB_SERIAL_OUT_RECV_PKT)) {
        USB_INT_CLR_REG = USB_SERIAL_OUT_RECV_PKT;
    }
    return c;
}

/* ── Print functions ── */
static void print(const char *s) {
    while (*s) {
        if (*s == '\n') usb_putc('\r');
        usb_putc(*s++);
    }
    usb_flush();
}

static void print_hex32(u32 val) {
    const char hex[] = "0123456789abcdef";
    print("0x");
    for (int i = 28; i >= 0; i -= 4)
        usb_putc(hex[(val >> i) & 0xF]);
    usb_flush();
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

static void print_dec(u32 val) {
    char buf[12];
    int len = itoa_dec(val, buf);
    for (int i = 0; i < len; i++) usb_putc(buf[i]);
    usb_flush();
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

/* ── Code Execution Buffer ──
 * Must be in RAM for execute permission.
 * ESP32-C3 RAM at 0x3FC80000 is executable. */
#define CODE_BUF_SIZE 2048
static u8 __attribute__((aligned(16))) code_buf[CODE_BUF_SIZE];
static char result_buf[256];
static int result_len = 0;

/* ── Virtual GPIO (for testing) ── */
#define VGPIO_COUNT 11
static u8 vgpio_pins[VGPIO_COUNT];

/* ── POKE Protocol ── */
#define RX_BUF_SIZE 4096
static u8 rx_buf[RX_BUF_SIZE];
static int rx_pos = 0;

static void send_frame(const char *magic, const u8 *payload, u32 len) {
    for (int i = 0; i < 4; i++) usb_putc(magic[i]);
    usb_putc(len & 0xFF);
    usb_putc((len >> 8) & 0xFF);
    usb_putc((len >> 16) & 0xFF);
    usb_putc((len >> 24) & 0xFF);
    for (u32 i = 0; i < len; i++) usb_putc(payload[i]);
    usb_flush();
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

/* ── Command Handlers ── */

static void handle_exec(const u8 *code, int code_len) {
    if (code_len <= 0 || code_len > CODE_BUF_SIZE) {
        send_resp_str("error: invalid code size");
        return;
    }

    mem_copy(code_buf, code, code_len);

    /* Check for RET instruction (0x8067 or 0x8082 compressed) */
    int has_ret = 0;
    for (int i = 0; i < code_len - 1; i++) {
        if (code_buf[i] == 0x67 && code_buf[i+1] == 0x80) { has_ret = 1; break; }
        if (code_buf[i] == 0x82 && code_buf[i+1] == 0x80) { has_ret = 1; break; }
    }

    mem_set(result_buf, 0, 256);
    result_len = 0;
    u32 ret_a0 = 0;

    if (has_ret) {
        /* ESP32-C3 memory mapping:
         *   DRAM0: 0x3FC80000 (data read/write)
         *   IRAM0: 0x40380000 (instruction fetch)
         * Same physical memory, different bus addresses.
         * Convert code_buf DRAM address to IRAM address for execution. */
        u32 dram_addr = (u32)code_buf;
        u32 iram_addr = dram_addr - 0x3FC80000 + 0x40380000;

        /* Flush instruction cache */
        __asm__ volatile("fence.i");

        /* Execute from IRAM address */
        u32 (*fn)(char *, int *) = (u32 (*)(char *, int *))(iram_addr);
        ret_a0 = fn(result_buf, &result_len);
    }

    char resp[128];
    int rlen = 0;
    if (!has_ret) {
        rlen = str_copy(resp, "no RET found, not executed");
    } else if (result_len > 0) {
        mem_copy(resp, result_buf, result_len);
        rlen = result_len;
    } else {
        rlen += str_copy(resp + rlen, "a0=");
        rlen += itoa_dec(ret_a0, resp + rlen);
    }
    send_resp((const u8 *)resp, rlen);
}

static void handle_info(void) {
    char resp[384];
    int n = 0;
    n += str_copy(resp + n, "{\"status\":\"alive\",\"arch\":\"rv32imc\",\"chip\":\"esp32c3\"");
    n += str_copy(resp + n, ",\"kernel\":\"poke-os\",\"transport\":\"usb-serial-jtag\"");
    n += str_copy(resp + n, ",\"commands\":[\"PING\",\"INFO\",\"EXEC\",\"GPIO\",\"GPOS\",\"TEMP\"]");
    n += str_copy(resp + n, ",\"bare_metal\":true,\"freertos\":false}");
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
    /* Virtual temperature for now — TODO: real sensor via REGI2C */
    static u32 tick = 0;
    tick++;
    u32 temp_x10 = 250 + (tick % 30);  /* 25.0 ~ 27.9 °C */
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

/* ── Parse POKE frames from rx_buf ── */
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
            } else {
                break;
            }
        } else {
            for (int i = 0; i < rx_pos - 1; i++) rx_buf[i] = rx_buf[i + 1];
            rx_pos--;
        }
    }
}

/* ── Kernel Main ── */
void kernel_main(void) {
    /* Disable watchdogs and memory protection */
    wdt_disable();
    pms_disable();

    /* PROOF OF LIFE: blink GPIO8 LED before anything else */
    /* GPIO_ENABLE_REG: 0x60004020 — set bit 8 to enable output */
    (*(volatile u32 *)0x60004020) |= (1 << 8);
    for (int blink = 0; blink < 10; blink++) {
        /* GPIO_OUT_REG: 0x60004004 — set/clear bit 8 */
        (*(volatile u32 *)0x60004004) |= (1 << 8);    /* LED on */
        for (volatile int i = 0; i < 500000; i++) {}
        (*(volatile u32 *)0x60004004) &= ~(1 << 8);   /* LED off */
        for (volatile int i = 0; i < 500000; i++) {}
    }

    /* Init USB-Serial/JTAG */
    usb_init();

    /* Repeatedly try to output — host may reconnect at any point */
    for (int attempt = 0; attempt < 30; attempt++) {
        const char *msg = "POKE\r\n";
        for (int i = 0; msg[i]; i++) {
            if (!(USB_EP1_CONF_REG & USB_SERIAL_IN_EP_DATA_FREE)) break;
            USB_EP1_REG = (u32)(u8)msg[i];
        }
        USB_EP1_CONF_REG = USB_WR_DONE;
        for (volatile int d = 0; d < 500000; d++) {}
    }

    print("\n");
    print("  ____   ___  _  _______ \n");
    print(" |  _ \\ / _ \\| |/ / ____|\n");
    print(" | |_) | | | | ' /|  _|  \n");
    print(" |  __/| |_| | . \\| |___ \n");
    print(" |_|    \\___/|_|\\_\\_____|\n");
    print("\n");
    print("  POKE OS ESP32-C3 v0.1 (bare-metal)\n");
    print("  arch: rv32imc\n");
    print("  kernel: poke-os (no FreeRTOS)\n");
    print("  transport: USB-Serial/JTAG @ 0x60043000\n");
    print("  code_buf: ");
    print_hex32((u32)code_buf);
    print("\n\n");

    print("poke-esp32> ");

    /* Main loop: read USB → parse POKE frames */
    while (1) {
        /* Read available bytes */
        while (usb_available() && rx_pos < RX_BUF_SIZE) {
            rx_buf[rx_pos++] = usb_getc();
        }

        /* Parse POKE frames */
        poll_protocol();

        /* Feed non-POKE bytes to simple shell */
        while (rx_pos > 0) {
            static const char magic[] = "POKE";
            int could_be_poke = 1;
            int check_len = rx_pos < 4 ? rx_pos : 4;
            for (int i = 0; i < check_len; i++) {
                if (rx_buf[i] != magic[i]) { could_be_poke = 0; break; }
            }
            if (could_be_poke) break;

            /* Not POKE — consume byte */
            char c = (char)rx_buf[0];
            for (int i = 0; i < rx_pos - 1; i++) rx_buf[i] = rx_buf[i + 1];
            rx_pos--;

            if (c == '\r' || c == '\n') {
                print("\npoke-esp32> ");
            }
        }
    }
}
