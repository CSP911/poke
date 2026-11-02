/* POKE sentence runtime v1: UART, result line, clock, power-off — and the fence.
 * Part of every sentence image and gone with it. Runs at EL1; the op runs at EL0
 * under the page table in pt.h and reaches this file only through svc (ustub.S).
 * This file is fixed and audited once; the op is whatever the model wrote. */
#include "rt.h"
#include "pt.h"
typedef unsigned int u32; typedef unsigned long u64;

#define R(a) (*(volatile u32 *)(a))
#define GPIO  0xFE200000UL
#define UART  0xFE201000UL
#define PM    0xFE100000UL
#define GICD  0xFF841000UL
#define GICC  0xFF842000UL
#define PM_PASS 0x5a000000u
#define TIMER_IRQ 30                                /* PPI 30: EL1 physical timer */
#ifndef OP_BUDGET_MS
#define OP_BUDGET_MS 20000                          /* the op's whole run, runtime calls included */
#endif

static void uart_pins(void) {                       /* GPIO 14/15 → ALT0 (TXD0/RXD0) */
    u32 sel = R(GPIO + 0x04);
    sel &= ~((7u << 12) | (7u << 15)); sel |= (4u << 12) | (4u << 15);
    R(GPIO + 0x04) = sel;
}
static void uart_init(void) {
    R(UART + 0x30) = 0;
    uart_pins();
    R(UART + 0x44) = 0x7FF;
    R(UART + 0x24) = 26; R(UART + 0x28) = 3;        /* 115200 @ 48 MHz */
    R(UART + 0x2C) = (3u << 5) | (1u << 4);
    R(UART + 0x30) = (1u << 0) | (1u << 8) | (1u << 9);
}
static void putc_(char c) { u32 t = 1000000; while ((R(UART + 0x18) & (1u << 5)) && --t) {} R(UART) = (u32)c; }
static void puts_(const char *s) { while (*s) { if (*s == '\n') putc_('\r'); putc_(*s++); } }
static void putdec(long v) {
    char t[24]; int n = 0; unsigned long u = v < 0 ? (unsigned long)(-v) : (unsigned long)v;
    if (v < 0) putc_('-');
    if (!u) t[n++] = '0';
    while (u) { t[n++] = (char)('0' + u % 10); u /= 10; }
    while (n) putc_(t[--n]);
}
static void puthex(u64 v) { puts_("0x"); for (int i = 60; i >= 0; i -= 4) putc_("0123456789abcdef"[(v >> i) & 15]); }

static u64 t0, frq, deadline;
static u64 cnt(void) { u64 v; __asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(v)); return v; }
static u64 k_ms(void) { return (cnt() - t0) * 1000 / frq; }

/* result line: POKE-RESULT {"k": v, ...} — built as the op reports */
/* D2: the op's fields stop 320 B short of the end (room for error + truncated + '}'); a field that
 * does not fit is rolled back whole and "truncated":true is added, so the line is always valid JSON */
#define RES_OP_CAP ((int)sizeof res - 320)
static char res[1024]; static int rn, rcap, rfull; static int failed; static char why[128];
static void rc(char c) { if (rn < rcap) res[rn++] = c; else rfull = 1; }
static void rs(const char *s) { while (*s) { char c = *s++; if (c == '"' || c == '\\') rc('\\'); if ((unsigned char)c >= 0x20) rc(c); } }
static void rkey(const char *k) { rc(rn ? ',' : '{'); rc('"'); rs(k); rc('"'); rc(':'); }
static void rdec(long v) { char t[24]; int n = 0; unsigned long u = v < 0 ? (unsigned long)(-v) : (unsigned long)v; if (v < 0) rc('-'); if (!u) t[n++] = '0'; while (u) { t[n++] = (char)('0' + u % 10); u /= 10; } while (n) rc(t[--n]); }
static void k_fail(const char *w) { failed = 1; int i = 0; while (w[i] && i < (int)sizeof why - 1) { why[i] = w[i]; i++; } why[i] = 0; }

/* power off: the PM watchdog performs a full reset (twin: a guest reset is a shutdown) */
__attribute__((noreturn)) static void power_off(void) {
    puts_("POKE-OFF\n");
    for (u32 t = 1000000; (R(UART + 0x18) & (1u << 3)) && --t; ) {}   /* drain the FIFO before the reset (real Pi cut the last line) */
    R(PM + 0x24) = PM_PASS | 10;
    R(PM + 0x1c) = PM_PASS | (R(PM + 0x1c) & ~0x30u) | 0x20;
    for (;;) __asm__ volatile("wfe");
}
__attribute__((noreturn)) static void finish(void) {
    __asm__ volatile("msr cntp_ctl_el0, xzr; isb");
    uart_pins();                                    /* the GPIO window lets an op move pins 14/15; take them back */
    rcap = (int)sizeof res - 2;
    if (rfull) { rkey("truncated"); rs("true"); }
    if (failed) { rkey("error"); rc('"'); rs(why); rc('"'); }
    if (!rn) rc('{');
    rc('}'); res[rn] = 0;
    puts_("POKE-RESULT "); puts_(res); puts_("\n");
    puts_("POKE-END ms="); putdec((long)k_ms()); puts_(failed ? " status=failed\n" : " status=ok\n");
    power_off();
}

/* ── reading what the op hands over: ldtrb loads with the op's (EL0) permissions, so a
 * pointer outside the op's pages faults here instead of reading the runtime ── */
static int in_ucopy;                                /* D4: no control bytes cross — a say() cannot start a line */
static void ustr(u64 a, char *dst, int max) {
    int i = 0; in_ucopy = 1;
    for (; i < max - 1; i++) { u32 c; __asm__ volatile("ldtrb %w0, [%1]" : "=r"(c) : "r"(a + (u64)i) : "memory"); if (!c) break; dst[i] = (char)(c < 0x20 || c == 0x7f ? '?' : c); }
    dst[i] = 0; in_ucopy = 0;
}

/* D1 (real Pi only, hub builds with -DPOKE_WDOG): the PM watchdog is armed at boot and re-armed on
 * every runtime call, ~15 s each (its maximum is ~16 s). EL1-only page: the op cannot touch it.
 * Not for the twin — QEMU resets the moment the watchdog is armed. Untested on hardware. */
#ifdef POKE_WDOG
static void wdog_kick(void) { R(PM + 0x24) = PM_PASS | (15u << 16); R(PM + 0x1c) = PM_PASS | (R(PM + 0x1c) & ~0x30u) | 0x20; }
#else
static void wdog_kick(void) {}
#endif

__attribute__((noreturn)) static void timeout(void) { puts_("POKE-FAULT kind=timeout\n"); k_fail("op ran past its time budget"); finish(); }

/* svc #nr from the op (ustub.S) */
u64 sentence_svc(u64 a0, u64 a1, u64 a2, u64 nr) {
    char k[64], s[200]; int m = rn;
    if (cnt() >= deadline) timeout();
    wdog_kick();
    switch (nr) {
    case 0: finish();                                                   /* op_main returned */
    case 1: ustr(a0, s, sizeof s); puts_("POKE-SAY "); puts_(s); puts_("\n"); return 0;
    case 2: ustr(a0, k, sizeof k); rkey(k); rdec((long)a1); if (rfull) rn = m; return 0;
    case 3: {
        long v = (long)a1, p = 1; int d = (int)a2; if (d < 0) d = 0; if (d > 18) d = 18;
        for (int i = 0; i < d; i++) p *= 10;
        ustr(a0, k, sizeof k); rkey(k); if (v < 0) { rc('-'); v = -v; } rdec(v / p);
        if (d) { rc('.'); long f = v % p; for (long q = p / 10; q; q /= 10) rc((char)('0' + (f / q) % 10)); }
        if (rfull) rn = m;
        return 0; }
    case 4: ustr(a0, k, sizeof k); ustr(a1, s, sizeof s); rkey(k); rc('"'); rs(s); rc('"'); if (rfull) rn = m; return 0;
    case 5: ustr(a0, s, sizeof s); k_fail(s); return 0;
    case 6: return k_ms();
    case 7: {
        u64 n = a0 > 10000 ? 10000 : a0, end = cnt() + n * frq / 1000;
        while (cnt() < end && cnt() < deadline) {}
        if (cnt() >= deadline) timeout();
        return 0; }
    }
    puts_("POKE-FAULT kind=call nr="); putdec((long)nr); puts_("\n");
    k_fail("op made a call the runtime does not have"); finish();
    return 0;
}

/* ── the fence ── */
extern char __rt_text_start[], __rt_text_end[], __op_x_end[], __op_r_end[], __op_guard[], __op_rw_end[], __rt_end[], __op_stack_top[];
static pt_u64 tables[PT_NTABLES][512] __attribute__((aligned(4096)));

static void mmu_on(void) {
    struct pt_layout L = {
        .rt_x0 = (u64)__rt_text_start, .rt_x1 = (u64)__rt_text_end,
        .op_x0 = (u64)__rt_text_end,   .op_x1 = (u64)__op_x_end,
        .op_r0 = (u64)__op_x_end,      .op_r1 = (u64)__op_r_end,
        .op_rw0 = (u64)__op_r_end, .op_guard = (u64)__op_guard, .op_rw1 = (u64)__op_rw_end,
        .rt_rw0 = (u64)__op_rw_end,    .rt_rw1 = (u64)__rt_end,
    };
    if (pt_build(tables, &L)) { k_fail("image layout does not fit the fence"); finish(); }
    __asm__ volatile("dsb sy");
    __asm__ volatile("msr mair_el1, %0" :: "r"(PT_MAIR));
    __asm__ volatile("msr tcr_el1, %0" :: "r"(PT_TCR));
    __asm__ volatile("msr ttbr0_el1, %0; isb" :: "r"((u64)tables[0]));
    __asm__ volatile("dsb sy; tlbi vmalle1; dsb sy; ic iallu; dsb sy; isb");
    /* M, SA, SA0, I, WXN on; caches for data stay off (no maintenance needed). EL0 gets no
     * DAIF access (UMA=0), no cache ops (UCI=0, DZE=0), no counter (UCT=0, CNTKCTL=0),
     * and wfi/wfe trap (nTWI=nTWE=0). */
    u64 sc = 0x30d00800UL | (1UL << 0) | (1UL << 3) | (1UL << 4) | (1UL << 12) | (1UL << 19);
    __asm__ volatile("msr sctlr_el1, %0; isb" :: "r"(sc));
    __asm__ volatile("msr cntkctl_el1, xzr; isb");
}

static void budget_on(void) {
    R(GICD + 0x000) = 0;
    R(GICD + 0x100) = 1u << TIMER_IRQ;                                      /* ISENABLER0: PPI 30 */
    u32 pr = R(GICD + 0x400 + 28); pr = (pr & ~(0xffu << 16)) | (0xa0u << 16); R(GICD + 0x400 + 28) = pr;
    R(GICD + 0x000) = 1;
    R(GICC + 0x004) = 0xf0; R(GICC + 0x008) = 0; R(GICC + 0x000) = 1;
    deadline = cnt() + (u64)OP_BUDGET_MS * frq / 1000;
    __asm__ volatile("msr cntp_cval_el0, %0" :: "r"(deadline));
    __asm__ volatile("msr cntp_ctl_el0, %0; isb" :: "r"(1UL));             /* enabled, unmasked */
}

extern void enter_el0(u64 entry, u64 sp_el0, u64 lr);
extern void op_return(void);

void sentence_boot(void) {
    __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(frq));
    if (!frq) frq = 54000000;
    t0 = cnt();
    uart_init();
    rcap = RES_OP_CAP;
    wdog_kick();
    puts_("POKE-BOOT\n");
    mmu_on();
    budget_on();
    enter_el0((u64)op_main, (u64)__op_stack_top, (u64)op_return);
}

void sentence_fault(u64 kind, u64 esr, u64 elr, u64 far) {
    u32 ec = (u32)(esr >> 26) & 0x3f;
    if (kind == 9) timeout();
    puts_("POKE-FAULT kind="); putdec((long)kind);
    puts_(" esr="); puthex(esr); puts_(" elr="); puthex(elr); puts_(" far="); puthex(far); puts_("\n");
    const char *w = "runtime faulted";
    if (kind == 4 && in_ucopy)  w = "op handed the runtime a pointer outside its pages";
    else if (kind == 8) {
        w = ec == 0x24 ? "op touched memory it was not given"
          : ec == 0x20 ? "op jumped outside its code"
          : ec == 0x01 ? "op tried to wait (wfi/wfe)"
          : ec == 0x18 || ec == 0x00 ? "op used an instruction an op may not"
          : "op faulted";
    }
    k_fail(w);
    finish();
}
