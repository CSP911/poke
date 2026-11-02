/* POKE sentence runtime v1: UART, result line, clock, power-off — and the fence.
 * Part of every sentence image and gone with it. Runs at EL1; the op runs at EL0
 * under the page table in pt.h and reaches this file only through svc (ustub.S).
 * This file is fixed and audited once; the op is whatever the model wrote. */
#include "rt.h"
#include "pt.h"
#include "rt_power.h"                          /* 실기기팀: watchdog, RCFG, power-off */
typedef unsigned int u32; typedef unsigned long u64;

#define R(a) (*(volatile u32 *)(a))
#define GPIO  0xFE200000UL
#define UART  0xFE201000UL
#define PM    0xFE100000UL
#define GICD  0xFF841000UL
#define GICC  0xFF842000UL
#define BSC1  0xFE804000UL                          /* I2C bus 1 — the runtime's since V5: the op asks by svc */
#define PM_PASS 0x5a000000u
#define TIMER_IRQ 30                                /* PPI 30: EL1 physical timer */

static void uart_pins(void) {                       /* GPIO 14/15 → ALT0 (TXD0/RXD0) */
    u32 sel = R(GPIO + 0x04);
    sel &= ~((7u << 12) | (7u << 15)); sel |= (4u << 12) | (4u << 15);
    R(GPIO + 0x04) = sel;
}
/* I2C bus 1 pins: GPIO 2/3 → ALT0 (SDA1/SCL1), pull-ups. Done here, before the op, so the op
 * needs no GPIO window at all (fragments only drive BSC1). */
static void i2c_pins(void) {
    u32 f = R(GPIO + 0x00); f &= ~((7u << 6) | (7u << 9)); f |= (4u << 6) | (4u << 9); R(GPIO + 0x00) = f;
    u32 p = R(GPIO + 0xE4); p &= ~((3u << 4) | (3u << 6)); p |= (1u << 4) | (1u << 6); R(GPIO + 0xE4) = p;
    R(BSC1 + 0x14) = rtp_cdiv();                    /* CDIV for 100 kHz from this target's core clock (RCFG) */
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

/* ── X1: what the node really did on the bus. Every I2C transfer the op asks for is noted here, by
 * the runtime, as it happens (refused ones too), and printed as its own line before the result. The op
 * cannot write that line: the UART is not in its pages and say() strings cannot start a line. The first
 * FX_MAX transfers are kept in full; per-device totals and the set of registers written are always
 * complete, so "truncated" never hides a write.
 * R1: the line carries the records as they are, in hex; the hub decodes them (say.parse_effects):
 *   POKE-EFFECTS2 <hex: header, then ndev × struct fxdev, then nx × struct fx>
 *   header  u32 total, u32 other_devices, u8 ndev, u8 nx, u8 FX_MAX, u8 version (2)
 *   fxdev   u32 writes, reads, denied; u8 addr, used; u8 regs[32] (bitmap: first byte of each ≥2-byte
 *           write), ptr[32] (bitmap: 1-byte pointer writes); 2 bytes padding — 80 bytes
 *   fx      u32 ms; u8 addr, read, status (0 ok, 1–4 bus error, 9 refused), nb; u32 n; u8 bytes[8] — 20 bytes */
#define FX_MAX 32
#define FX_DEV 9
struct fx { u32 ms; unsigned char addr, rd, st, nb; u32 n; unsigned char b[8]; };
struct fxdev { u32 w, r, denied; unsigned char addr, used, regs[32], ptr[32]; };
static struct fx fx[FX_MAX];
static struct { u32 total, other; unsigned char ndev, nx, max, ver; } fxh;
static struct fxdev fxd[FX_DEV];
static void fx_note(u32 addr, int rd, const unsigned char *b, u32 n, u32 st) {
    if (fxh.nx < FX_MAX) {
        struct fx *e = &fx[fxh.nx++];
        e->ms = (u32)k_ms(); e->addr = (unsigned char)addr; e->rd = (unsigned char)rd; e->st = (unsigned char)st; e->n = n;
        e->nb = (unsigned char)(n < 8 ? n : 8);
        for (u32 i = 0; i < e->nb; i++) e->b[i] = b[i];
    }
    fxh.total++;
    struct fxdev *d = 0;
    for (int i = 0; i < fxh.ndev && !d; i++) if (fxd[i].addr == addr) d = &fxd[i];
    if (!d && fxh.ndev < FX_DEV) { d = &fxd[fxh.ndev++]; d->used = 1; d->addr = (unsigned char)addr; }
    if (!d) { fxh.other++; return; }
    if (st == 9) d->denied++;
    if (rd) { d->r++; return; }
    d->w++;
    if (n) (n >= 2 ? d->regs : d->ptr)[b[0] >> 3] |= (unsigned char)(1u << (b[0] & 7));
}
static void hexb(const void *p, u32 n) {
    const unsigned char *c = p;
    for (u32 i = 0; i < n; i++) { putc_("0123456789abcdef"[c[i] >> 4]); putc_("0123456789abcdef"[c[i] & 15]); }
}
static void fx_print(void) {
    fxh.max = FX_MAX; fxh.ver = 2;
    puts_("POKE-EFFECTS2 ");
    hexb(&fxh, 12); hexb(fxd, fxh.ndev * sizeof fxd[0]); hexb(fx, fxh.nx * sizeof fx[0]);
    puts_("\n");
}
_Static_assert(sizeof(struct fx) == 20 && sizeof(struct fxdev) == 80 && sizeof fxh == 12, "POKE-EFFECTS2 layout");

extern __attribute__((noreturn)) void scrub_stack_then(void (*fn)(void));   /* boot.S */
/* power off: the PM watchdog performs a full reset (twin: a guest reset is a shutdown) */
__attribute__((noreturn)) static void power_off(void) {
    puts_("POKE-OFF\n");
    scrub_stack_then(rtp_power_off);                /* stack zeroed here; rt_power: drain, zero the rest, partition, reset */
}
__attribute__((noreturn)) static void finish(void) {
    __asm__ volatile("msr cntp_ctl_el0, xzr; isb");
    rcap = (int)sizeof res - 2;
    if (rfull) { rkey("truncated"); rs("true"); }
    if (failed) { rkey("error"); rc('"'); rs(why); rc('"'); }
    if (!rn) rc('{');
    rc('}'); res[rn] = 0;
    fx_print();
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
static void uget(u64 a, unsigned char *dst, u32 n) {         /* bytes from the op, with its permissions */
    in_ucopy = 1;
    for (u32 i = 0; i < n; i++) { u32 c; __asm__ volatile("ldtrb %w0, [%1]" : "=r"(c) : "r"(a + i) : "memory"); dst[i] = (unsigned char)c; }
    in_ucopy = 0;
}
static void uput(u64 a, const unsigned char *src, u32 n) {   /* bytes to the op, with its permissions */
    in_ucopy = 1;
    for (u32 i = 0; i < n; i++) __asm__ volatile("sttrb %w0, [%1]" :: "r"((u32)src[i]), "r"(a + i) : "memory");
    in_ucopy = 0;
}

/* ── I2C by request (V5). The hub writes the image's allow list into i2c_allow (find "I2CA",
 * fill n and the devices): from the op's MANIFEST, or else from the fragments it includes minus
 * what policy denies. Unpatched = no device. The table is runtime rodata — EL0 cannot see it. ── */
#define I2CA_MAX 8
/* V6: per writable register a value range [lo, hi]; per device, at most max_count writes of ≥ 2 bytes in
 * this run (0xFFFF = no cap). 48 bytes per device; say.patch_i2c_allow writes it.
 * Every data byte of a write is checked, not just the first (V6b). read bit 2 (auto-increment known):
 * data byte j lands in register first+j-1, which must itself be writable and in its own range. Bit 2
 * clear (unknown, or the part does not auto-increment): every data byte must lie in the range of the
 * register the write names — the safe reading when we do not know where the bytes go. */
struct i2c_dev { unsigned char addr, read, max_write, nregs; unsigned short max_count, pad;
                 unsigned char regs[12], lo[12], hi[12], pad2[4]; };
_Static_assert(sizeof(struct i2c_dev) == 48, "I2CA layout");
static unsigned short i2c_count[I2CA_MAX];
static const volatile struct { u32 magic, n; struct i2c_dev dev[I2CA_MAX]; } i2c_allow
    __attribute__((section(".rodata.i2callow"), used, aligned(16))) = { 0x41433249u /* "I2CA" */, 0, {{0}} };

static const char *i2c_denied(u32 addr, int rd, const unsigned char *b, u32 n) {
    u32 cnt_ = i2c_allow.n > I2CA_MAX ? I2CA_MAX : i2c_allow.n;
    for (u32 i = 0; i < cnt_; i++) {
        const volatile struct i2c_dev *d = &i2c_allow.dev[i];
        if (d->addr != addr) continue;
        /* read: bit 0 = reads, bit 1 = writes of 0–1 byte (a register pointer, or an address probe).
         * A scan of unknown parts gets bit 0 only: on some parts a 1-byte write is itself an action. */
        if (rd) return (d->read & 1) ? 0 : "op read a device its manifest does not let it read";
        if (n <= 1) return (d->read & 2) ? 0 : "op wrote to a device it may only read";
        if (n > d->max_write) return "op wrote more bytes than its manifest allows for this device";
        for (u32 k = 0; k < d->nregs && k < 12; k++) {
            if (d->regs[k] != b[0]) continue;
            for (u32 j = 1; j < n; j++) {
                u32 kk = k;
                if (d->read & 4) {
                    u32 r = (b[0] + j - 1) & 0xff;
                    for (kk = 0; kk < d->nregs && kk < 12 && d->regs[kk] != r; kk++) {}
                    if (kk >= d->nregs || kk >= 12) return "op wrote past the registers its manifest allows";
                }
                if (b[j] < d->lo[kk] || b[j] > d->hi[kk]) return "op wrote a value outside the range its manifest allows";
            }
            if (i2c_count[i] >= d->max_count) return "op wrote to this device more times than its manifest allows";
            i2c_count[i]++;
            return 0;
        }
        return "op wrote a register its manifest does not allow";
    }
    return "op used an I2C device that is not in its manifest";
}

/* one transfer on BSC1: 0 ok, 1 bad request, 2 no ACK, 3 clock stretch, 4 never finished */
static u64 k_i2c(u32 addr, int rd, u64 ubuf, u32 n) {
    unsigned char b[64];
    if (n > sizeof b || addr > 0x7f) return 1;
    if (!rd) uget(ubuf, b, n);
    const char *no = i2c_denied(addr, rd, b, n);
    if (no) {
        puts_("POKE-FAULT kind=i2c addr="); puthex(addr); puts_(rd ? " read" : " write reg="); if (!rd) puthex(n ? b[0] : 0);
        puts_(" n="); putdec((long)n); puts_("\n");
        fx_note(addr, rd, b, n, 9);
        k_fail(no); finish();
    }
    R(BSC1 + 0x04) = 2u | (1u << 8) | (1u << 9);
    R(BSC1 + 0x0C) = addr; R(BSC1 + 0x08) = n;
    R(BSC1 + 0x00) = (1u << 15) | (1u << 7) | (rd ? (1u | (3u << 4)) : 0);
    u32 k = 0, st = 0;
    for (int i = 0; i < 20000; i++) {
        st = R(BSC1 + 0x04);
        if (rd) { while (k < n && (R(BSC1 + 0x04) & (1u << 5))) b[k++] = (unsigned char)R(BSC1 + 0x10); }
        else    { while (k < n && (R(BSC1 + 0x04) & (1u << 4))) R(BSC1 + 0x10) = b[k++]; }
        if (st & ((1u << 8) | (1u << 9))) break;
        if ((st & 2u) && k == n) break;
        for (volatile int j = 0; j < 50; j++) {}
    }
    R(BSC1 + 0x04) = 2u | (1u << 8) | (1u << 9);
    u32 rv = (st & (1u << 8)) ? 2 : (st & (1u << 9)) ? 3 : !(st & 2u) ? 4 : 0;
    fx_note(addr, rd, b, rd ? k : n, rv);           /* before handing bytes back: a bad buffer still leaves the record */
    if (rd) uput(ubuf, b, n);
    return rv;
}

/* D1: the watchdog, the time budget and the reset belong to rt_power.c (실기기팀): budget and quiet
 * limit come from the image's RCFG block (sentence/rtcfg.py), not from this file. */

__attribute__((noreturn)) static void timeout(void) {
    const char *quiet = rtp_timeout_reason(cnt(), deadline);
    puts_("POKE-FAULT kind=timeout\n"); k_fail(quiet ? quiet : "op ran past its time budget"); finish();
}

/* svc #nr from the op (ustub.S) */
u64 sentence_svc(u64 a0, u64 a1, u64 a2, u64 a3, u64 nr) {
    char k[64], s[200]; int m = rn;
    if (cnt() >= deadline) timeout();
    rtp_kick();
    __asm__ volatile("msr cntp_cval_el0, %0; isb" :: "r"(rtp_compare(cnt(), frq, deadline)));
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
        if (a0 > 10000) {                                               /* D9: never cut a wait short silently */
            puts_("POKE-FAULT kind=delay n="); putdec((long)a0); puts_("\n");
            k_fail("delay_ms over 10000 ms: one wait is at most 10 s and a run at most 20 s; longer waits belong to the hub");
            finish();
        }
        u64 n = a0, end = cnt() + n * frq / 1000;
        while (cnt() < end && cnt() < deadline) {}
        if (cnt() >= deadline) timeout();
        return 0; }
    case 8: return k_i2c((u32)a0, a1 != 0, a2, (u32)a3);
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
    deadline = cnt() + (u64)rtp_budget_ms() * frq / 1000;
    __asm__ volatile("msr cntp_cval_el0, %0" :: "r"(rtp_compare(cnt(), frq, deadline)));
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
    rtp_boot();
    puts_("POKE-BOOT\n");
    i2c_pins();
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
