/* POKE sentence runtime for an ESP32-C3 reached through its ROM download mode.
 *
 * The chip keeps nothing of ours: its strapping pins hold it in the ROM's serial download mode; the hub
 * writes this image straight into RAM (esptool load_ram — no flash write) and the ROM jumps to it. The
 * runtime runs the op, prints the same lines as the Pi and x86 runtimes (POKE-BOOT, POKE-SAY,
 * POKE-RESULT {json}, POKE-END ms= status=, POKE-OFF) on UART0 through the ROM's own UART routine,
 * zeroes the RAM the run used, and resets the chip in software — which, still strapped for download,
 * comes back up in the ROM waiting for the next image.
 *
 * v0: no isolation yet (the op runs in machine mode next to the runtime); the RISC-V PMP fence the Pi and
 * x86 runtimes have is the next step. Time: the system timer (16 MHz) for ms(), the ROM's ets_delay_us.
 */
typedef unsigned int u32; typedef unsigned char u8; typedef unsigned long long u64;

/* the chip's ROM (components/esp_rom/esp32c3/ld/esp32c3.rom.ld) */
#define ROM(addr, ret, ...) ((ret (*)(__VA_ARGS__))(addr))
#define uart_tx_one_char   ROM(0x40000068, int, u8)
#define uart_tx_wait_idle  ROM(0x40000084, void, u8)
#define ets_delay_us       ROM(0x40000050, void, u32)
#define software_reset     ROM(0x40000090, void, void)

/* system timer, unit 0: 16 MHz */
#define SYST 0x60023000u
#define R(o) (*(volatile u32 *)(SYST + (o)))
static u64 ticks16(void) {
    R(0x04) = 1u << 30;                                  /* UNIT0_OP: update the value registers */
    for (int i = 0; i < 1000 && !(R(0x04) & (1u << 29)); i++) {}   /* VALUE_VALID */
    return ((u64)(R(0x40) & 0xFFFFF) << 32) | R(0x44);   /* UNIT0_VALUE_HI (20 bits), _LO */
}
static u64 t0;

static void putc_(char c) { uart_tx_one_char((u8)c); }
static void puts_(const char *s) { while (*s) { if (*s == '\n') putc_('\r'); putc_(*s++); } }
static void putdec(long v) { char t[16]; int n = 0; unsigned long u = v < 0 ? (unsigned long)-v : (unsigned long)v;
    if (v < 0) { putc_('-'); }
    if (!u) { t[n++] = '0'; }
    while (u) { t[n++] = (char)('0' + u % 10); u /= 10; } while (n) putc_(t[--n]); }

unsigned long ms(void) { return (unsigned long)((ticks16() - t0) / 16000); }
#define BUDGET_MS 20000u

/* result line — the same rules as the Pi and x86 runtimes */
static char res[1024]; static int rn, rcap = (int)sizeof res - 320, rfull; static int failed; static char why[128];
static void rc(char c) { if (rn < rcap) res[rn++] = c; else rfull = 1; }
static void rs(const char *s) { while (*s) { char c = *s++; if (c == '"' || c == '\\') rc('\\'); if ((u8)c >= 0x20) rc(c); } }
static void rkey(const char *k) { rc(rn ? ',' : '{'); rc('"'); rs(k); rc('"'); rc(':'); }
static void rdec(long v) { char t[16]; int n = 0; unsigned long u = v < 0 ? (unsigned long)-v : (unsigned long)v;
    if (v < 0) { rc('-'); }
    if (!u) { t[n++] = '0'; }
    while (u) { t[n++] = (char)('0' + u % 10); u /= 10; } while (n) rc(t[--n]); }
static void k_fail(const char *w) { failed = 1; int i = 0; for (; w[i] && i < (int)sizeof why - 1; i++) why[i] = w[i]; why[i] = 0; }
static void clean(char *d, const char *s, int max) { int i = 0; for (; s[i] && i < max - 1; i++) d[i] = (char)((u8)s[i] < 0x20 || s[i] == 0x7f ? '?' : s[i]); d[i] = 0; }

void say(const char *s) { char b[200]; clean(b, s, sizeof b); puts_("POKE-SAY "); puts_(b); puts_("\n"); }
void report_int(const char *key, long v) { int m = rn; char k[64]; clean(k, key, sizeof k); rkey(k); rdec(v); if (rfull) rn = m; }
void report_fixed(const char *key, long v, int d) {
    int m = rn; long p = 1; char k[64]; clean(k, key, sizeof k);
    if (d < 0) d = 0; if (d > 9) d = 9;
    for (int i = 0; i < d; i++) p *= 10;
    rkey(k); if (v < 0) { rc('-'); v = -v; } rdec(v / p);
    if (d) { rc('.'); long f = v % p; for (long q = p / 10; q; q /= 10) rc((char)('0' + (f / q) % 10)); }
    if (rfull) rn = m;
}
void report_str(const char *key, const char *s) { int m = rn; char k[64], b[200]; clean(k, key, sizeof k); clean(b, s, sizeof b);
    rkey(k); rc('"'); rs(b); rc('"'); if (rfull) rn = m; }
void fail(const char *w) { char b[128]; clean(b, w, sizeof b); k_fail(b); }

extern char __run_start[], __run_end[];
extern __attribute__((noreturn)) void scrub_and_reset(u32 start, u32 end);
__attribute__((noreturn)) static void finish(void);

void delay_ms(unsigned long n) {
    if (n > 10000) {
        puts_("POKE-FAULT kind=delay n="); putdec((long)n); puts_("\n");
        k_fail("delay_ms over 10000 ms: one wait is at most 10 s and a run at most 20 s; longer waits belong to the hub");
        finish();
    }
    unsigned long end = ms() + n;                         /* the system timer (crystal), not the ROM's cycle-counting delay */
    while (ms() < end) { if (ms() >= BUDGET_MS) { puts_("POKE-FAULT kind=timeout\n"); k_fail("op ran past its time budget"); finish(); } }
}

__attribute__((noreturn)) static void finish(void) {
    rcap = (int)sizeof res - 2;
    if (rfull) { rkey("truncated"); rs("true"); }
    if (failed) { rkey("error"); rc('"'); rs(why); rc('"'); }
    if (!rn) rc('{');
    rc('}'); res[rn] = 0;
    puts_("POKE-RESULT "); puts_(res); puts_("\n");
    puts_("POKE-END ms="); putdec((long)ms()); puts_(failed ? " status=failed\n" : " status=ok\n");
    puts_("POKE-OFF\n");
    uart_tx_wait_idle(0);
    scrub_and_reset((u32)__run_start, (u32)__run_end);   /* the op's code and data, the result, this stack: zero */
}

void op_main(void);
void sentence_boot(void) {
    R(0x00) |= (1u << 31) | (1u << 30);                  /* SYSTIMER_CONF: clock on, unit 0 counting */
    t0 = ticks16();
    puts_("POKE-BOOT esp32c3\n");
    op_main();
    finish();
}

/* the ROM's software reset, reached from boot.S after the scrub (no stack needed) */
void rom_reset(void) { software_reset(); for (;;) {} }
