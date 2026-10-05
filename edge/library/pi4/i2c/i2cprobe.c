/* I2C incubation probe — EL0 unit, maps one BSC (Broadcom Serial Controller).
 * Scans the bus for devices that ACK, then reads a TMP105-style temperature
 * register (pointer 0x00, 2 bytes) from P[1] if it answered.
 * P[0]: controller base (default 0xFE804000 = BSC1, GPIO 2/3 on a real Pi 4). */
typedef unsigned char u8; typedef unsigned int u32; typedef unsigned long u64;
static long sys(long n, long a, long b, long c) {
    register long x8 __asm__("x8") = n; register long x0 __asm__("x0") = a;
    register long x1 __asm__("x1") = b; register long x2 __asm__("x2") = c;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory"); return x0;
}
static void logs(const char *s) { sys(0, (long)s, 0, 0); }
static void exit_(long c) { sys(1, c, 0, 0); for (;;) {} }
static char lb[100] = "x"; static int lp;
static void lc(char c) { if (lp < 99) lb[lp++] = c; }
static void ls(const char *s) { while (*s) lc(*s++); }
static void lh(u32 v, int d) { for (int i = d - 1; i >= 0; i--) lc("0123456789abcdef"[(v >> (i * 4)) & 0xf]); }
static void ld(u32 v) { char t[12]; int n = 0; if (!v) t[n++] = '0'; while (v) { t[n++] = '0' + v % 10; v /= 10; } while (n) lc(t[--n]); }
static void lflush(void) { lb[lp] = 0; logs(lb); lp = 0; }

static volatile u64 P[2] = { 0x7a57a0017a57a001ULL, 0x7a57a0027a57a002ULL };
static u64 base;
#define R(off) (*(volatile u32 *)(base + (off)))
#define C 0x00
#define S 0x04
#define DLEN 0x08
#define A 0x0C
#define FIFO 0x10
#define DIV 0x14
#define S_TA 1u
#define S_DONE 2u
#define S_TXD (1u << 4)
#define S_RXD (1u << 5)
#define S_ERR (1u << 8)
#define S_CLKT (1u << 9)
#define C_EN (1u << 15)
#define C_ST (1u << 7)
#define C_CLEAR (3u << 4)
#define C_READ 1u

/* one transfer; returns 0 ok, 1 no ACK (ERR), 2 clock stretch timeout, 3 never finished.
 * Sequence that both the silicon and QEMU's model accept: address and length first,
 * then exactly ONE write to C (QEMU begins a transfer on any C write with I2CEN set),
 * then feed/drain the FIFO while watching S until DONE. */
static int xfer(u32 addr, int read, u8 *buf, u32 n) {
    R(S) = S_DONE | S_ERR | S_CLKT;
    R(A) = addr; R(DLEN) = n;
    R(C) = C_EN | C_ST | (read ? (C_READ | C_CLEAR) : 0);
    u32 done_n = 0, st = 0;
    for (int i = 0; i < 20000; i++) {
        st = R(S);
        if (read) { while (done_n < n && (R(S) & S_RXD)) buf[done_n++] = (u8)R(FIFO); }
        else      { while (done_n < n && (R(S) & S_TXD)) R(FIFO) = buf[done_n++]; }
        if (st & (S_ERR | S_CLKT)) break;
        if ((st & S_DONE) && done_n == n) break;
        for (volatile int k = 0; k < 50; k++) {}
    }
    R(S) = S_DONE | S_ERR | S_CLKT;
    if (st & S_ERR) return 1;
    if (st & S_CLKT) return 2;
    if (!(st & S_DONE)) return 3;
    return 0;
}

__attribute__((section(".text.main")))
void _start(void) {
    base = P[0] == 0x7a57a0017a57a001ULL ? 0xFE804000ULL : P[0];
    u32 dev = P[1] == 0x7a57a0027a57a002ULL ? 0x48 : (u32)P[1];
    ls("bsc @"); lh((u32)(base >> 4), 7); ls("0: C="); lh(R(C), 8); ls(" S="); lh(R(S), 8); ls(" DIV="); lh(R(DIV), 8); lflush();
    R(DIV) = 1500;                                   /* 150 MHz core / 1500 = 100 kHz */
    int found = 0; u8 b[4] = { 0 }; u32 codes[4] = { 0, 0, 0, 0 };
    ls("scan:");
    for (u32 a = 0x08; a < 0x78; a++) {              /* 1-byte read: does anyone ACK the address? */
        int r = xfer(a, 1, b, 1); codes[r & 3]++;
        if (r == 0) { ls(" 0x"); lh(a, 2); found++; }
    }
    ls("  [ok "); ld(codes[0]); ls(" nak "); ld(codes[1]); ls(" clkt "); ld(codes[2]); ls(" hung "); ld(codes[3]); lc(']');
    if (!found) ls(" (nothing answered)");
    lflush();
    if (found) {
        b[0] = 0x00;                                 /* TMP105: pointer → temperature register */
        int r = xfer(dev, 0, b, 1);
        if (r == 0) r = xfer(dev, 1, b, 2);
        if (r) { ls("read 0x"); lh(dev, 2); ls(": failed ("); ld(r); ls(")"); lflush(); exit_(2); }
        int raw = (int)(((b[0] << 8) | b[1]) >> 4); if (raw & 0x800) raw -= 0x1000;
        int mdeg = raw * 625 / 10;
        ls("tmp105 @0x"); lh(dev, 2); ls(": raw "); lh(b[0], 2); lh(b[1], 2); ls(" = ");
        if (mdeg < 0) { lc('-'); mdeg = -mdeg; } ld(mdeg / 1000); lc('.'); ld((mdeg % 1000) / 10); ls(" C"); lflush();
    }
    exit_(found ? 0 : 1);
}
