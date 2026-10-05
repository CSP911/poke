/* Resident driver: SD card (BCM2711 EMMC2, SDHCI, PIO).
 * Injected at runtime with `poke resident sdcard`; never linked into the kernel.
 *
 * Service ops (hub → SREQ → service page → here):
 *   1 SD_INFO   → rsp: u32 sdhc, u32 capacity_mb, u32 cid[4], u32 rca
 *   2 SD_READ   req: u32 lba, u32 count (1..2)      → rsp: count*512 bytes
 *   3 SD_WRITE  req: u32 lba, u8 data[512]          → status only
 * status: 0 ok, 1 bad request, 2 card error, 3 not initialised. */
#include "../../../kernel/pi4/poke_api.h"

static const api_t *A;
static void sd_log(const char *s) { if (A) A->log(s); }
static long sys3(long n, long a, long b, long c) {
    register long x8 __asm__("x8") = n; register long x0 __asm__("x0") = a;
    register long x1 __asm__("x1") = b; register long x2 __asm__("x2") = c;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory"); return x0;
}
static long sd_mbox(void *buf, long len) { return sys3(4, (long)buf, len, 0); }
#include "sdhci.h"

static sd_t card = { .bits = 1 };
static int ready = 0;
static sd_u32 blk[256] = { 1 };
static sd_u32 last_seq = 0;

static void mcpy(void *d, const void *s, int n) { sd_u8 *a = d; const sd_u8 *b = s; while (n--) *a++ = *b++; }
static sd_u32 rd32(const sd_u8 *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((sd_u32)p[3] << 24); }
static void wr32(sd_u8 *p, sd_u32 v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }

static void serve(svc_page_t *sp) {
    sd_u32 st = 0, rl = 0;
    if (!ready) st = 3;
    else switch (sp->op) {
    case 1:
        wr32(sp->rsp, card.sdhc); wr32(sp->rsp + 4, card.capacity_mb);
        for (int i = 0; i < 4; i++) wr32(sp->rsp + 8 + i * 4, card.cid[i]);
        wr32(sp->rsp + 24, card.rca); rl = 28; break;
    case 2: {
        if (sp->req_len < 8) { st = 1; break; }
        sd_u32 lba = rd32(sp->req), n = rd32(sp->req + 4);
        if (n < 1 || n > 2) { st = 1; break; }
        for (sd_u32 i = 0; i < n && !st; i++) if (sd_read(&card, lba + i, blk + i * 128)) st = 2;
        if (!st) { mcpy(sp->rsp, blk, n * 512); rl = n * 512; }
        break; }
    case 3: {
        if (sp->req_len < 4 + 512) { st = 1; break; }
        mcpy(blk, sp->req + 4, 512);
        if (sd_write(&card, rd32(sp->req), blk)) st = 2;
        break; }
    default: st = 1;
    }
    sp->rsp_len = rl; sp->status = st;
    __asm__ volatile("dsb sy" ::: "memory");
    sp->rsp_seq = sp->req_seq;
}

__attribute__((section(".text.main")))
unsigned long resident_main(const api_t *api, unsigned long op, unsigned long arg) {
    (void)arg;
    switch (op) {
    case RES_NAME: return (unsigned long)"sdcard";
    case RES_INIT: {
        A = api;
        int r = sd_init(&card);
        if (r) { sdl_s("sd: init failed ("); sdl_d((sd_u32)-r); sdl_s(")"); sdl_end(); return 1; }
        ready = 1; return 0; }
    case RES_TICK: {
        svc_page_t *sp = (svc_page_t *)UNIT_SVC_VA;
        if (sp->req_seq != last_seq) { last_seq = sp->req_seq; serve(sp); }
        return 0; }
    case RES_STOP: ready = 0; return 0;
    }
    return 0;
}
