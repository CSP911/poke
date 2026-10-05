/* SD card incubation probe — EL0 unit, maps only the EMMC2 controller.
 *
 * Walks the SDHCI bring-up by hand and logs every step, so a failure says
 * exactly where the controller or card stopped cooperating:
 *   reset → clock 400 kHz → CMD0 → CMD8 → ACMD41 → CMD2 → CMD3 → CMD7
 *   → 25 MHz, 4-bit → read block 0 → (optional) write/read-back a scratch block
 *
 * Capabilities: EMMC2 at 0xFE340000 (device). Mailbox via SYS_MBOX for the
 * controller's base clock. Nothing else is mapped. */
typedef unsigned char u8; typedef unsigned int u32; typedef unsigned long u64;

/* ── syscalls ── */
static long sys(long n, long a, long b, long c) {
    register long x8 __asm__("x8") = n; register long x0 __asm__("x0") = a;
    register long x1 __asm__("x1") = b; register long x2 __asm__("x2") = c;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory"); return x0;
}
static void logs(const char *s) { sys(0, (long)s, 0, 0); }
static void exit_(long c) { sys(1, c, 0, 0); for (;;) {} }
static void sd_log(const char *s) { logs(s); }
static long sd_mbox(void *buf, long len) { return sys(4, (long)buf, len, 0); }
#include "sdhci.h"
#define ls sdl_s
#define lh sdl_h
#define ld sdl_d
#define lflush sdl_end

static sd_t card = { .bits = 1 };
static u32 blk[128] = { 1 };

/* P[0]: 0 = read-only probe, 1 = also write a scratch block (lba P[1]) and read it back */
static volatile u64 P[2] = { 0x7a57a0017a57a001ULL, 0x7a57a0027a57a002ULL };

__attribute__((section(".text.main")))
void _start(void) {
    u64 mode = P[0] == 0x7a57a0017a57a001ULL ? 0 : P[0];
    u64 scratch = P[1] == 0x7a57a0027a57a002ULL ? 0 : P[1];

    int r = sd_init(&card);
    if (r) { ls("init failed at step "); ld((u32)-r); lflush(); exit_((u32)-r); }
    int sdhc = card.sdhc;
    if (sd_read(&card, 0, blk)) exit_(11);
    ls("block 0: "); for (int i = 0; i < 16; i++) lh(((u8 *)blk)[i], 2); ls(" ... sig "); lh(((u8 *)blk)[510], 2); lh(((u8 *)blk)[511], 2); lflush();
    if (((u8 *)blk)[510] == 0x55 && ((u8 *)blk)[511] == 0xAA) {
        for (int p = 0; p < 4; p++) {
            u8 *e = (u8 *)blk + 446 + p * 16; u32 type = e[4];
            if (!type) continue;
            u32 start = e[8] | (e[9] << 8) | (e[10] << 16) | ((u32)e[11] << 24), n = e[12] | (e[13] << 8) | (e[14] << 16) | ((u32)e[15] << 24);
            ls("MBR part "); ld(p); ls(": type 0x"); lh(type, 2); ls(" start "); ld(start); ls(" len "); ld(n); ls(" ("); ld(n / 2048); ls(" MB)"); lflush();
        }
    }
    (void)sdhc;
    if (mode == 1 && scratch) {
        if (sd_read(&card, scratch, blk)) exit_(12);
        u32 keep0 = blk[0];
        for (int i = 0; i < 128; i++) blk[i] = 0x504F4B45u ^ (i * 0x01010101u) ^ (u32)scratch;
        if (sd_write(&card, scratch, blk)) exit_(13);
        for (int i = 0; i < 128; i++) blk[i] = 0;
        if (sd_read(&card, scratch, blk)) exit_(14);
        int bad = 0; for (int i = 0; i < 128; i++) if (blk[i] != (0x504F4B45u ^ (i * 0x01010101u) ^ (u32)scratch)) bad++;
        ls("scratch "); ld(scratch); ls(": write+readback "); ls(bad ? "MISMATCH " : "ok"); if (bad) ld(bad); ls(" (was "); lh(keep0, 8); ls(")"); lflush();
        if (bad) exit_(15);
    }
    exit_(0);
}
