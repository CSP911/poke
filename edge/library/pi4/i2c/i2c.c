/* Resident driver: BCM2711 I2C bus (BSC1 = GPIO 2/3 on a Pi 4; the twin's i2c-bus.1).
 * Injected with `poke resident i2c`; never linked into the kernel.
 *
 * A bus service, not a device driver: the hub (and the language model) talk
 * to whatever sits on the bus through it, which is how an unknown device
 * gets incubated — scan, read its registers, work out what it is.
 *
 * Service ops (SREQ):
 *   1 SCAN                               → rsp: u8 count, u8 addr[count]
 *   2 READ   req: u8 addr, u8 reg, u8 n   → rsp: n bytes   (n ≤ 64; reg 0xFF = no register write first)
 *   3 WRITE  req: u8 addr, u8 data[]      → status only
 * status: 0 ok, 1 bad request, 2 no ACK, 3 clock stretch timeout, 4 transfer never finished. */
#include "../../../kernel/pi4/poke_api.h"
typedef unsigned char u8; typedef unsigned int u32; typedef unsigned long u64;

static const api_t *A;
#define GPIO_BASE 0xFE200000UL
#define GPFSEL0 (*(volatile u32 *)(GPIO_BASE + 0x00))
#define GPIO_PUP_PDN0 (*(volatile u32 *)(GPIO_BASE + 0xE4))
static void pins_alt0(void) {                      /* GPIO 2 = SDA1, GPIO 3 = SCL1: function ALT0 (100), pull-up (01) */
    u32 f = GPFSEL0; f &= ~((7u << 6) | (7u << 9)); f |= (4u << 6) | (4u << 9); GPFSEL0 = f;
    u32 p = GPIO_PUP_PDN0; p &= ~((3u << 4) | (3u << 6)); p |= (1u << 4) | (1u << 6); GPIO_PUP_PDN0 = p;
}
#ifndef BSC_BASE
#define BSC_BASE 0xFE804000UL
#endif
#define R(off) (*(volatile u32 *)(BSC_BASE + (off)))
#define C 0x00
#define S 0x04
#define DLEN 0x08
#define ADDR 0x0C
#define FIFO 0x10
#define DIV 0x14
#define S_DONE 2u
#define S_TXD (1u << 4)
#define S_RXD (1u << 5)
#define S_ERR (1u << 8)
#define S_CLKT (1u << 9)
#define C_EN (1u << 15)
#define C_ST (1u << 7)
#define C_CLEAR (3u << 4)
#define C_READ 1u

/* one transfer: address and length first, exactly one write to C, then feed/drain the FIFO
 * while watching S (the silicon and QEMU's model both accept this order) */
static int xfer(u32 addr, int read, u8 *buf, u32 n) {
    R(S) = S_DONE | S_ERR | S_CLKT;
    R(ADDR) = addr; R(DLEN) = n;
    R(C) = C_EN | C_ST | (read ? (C_READ | C_CLEAR) : 0);
    u32 k = 0, st = 0;
    for (int i = 0; i < 20000; i++) {
        st = R(S);
        if (read) { while (k < n && (R(S) & S_RXD)) buf[k++] = (u8)R(FIFO); }
        else      { while (k < n && (R(S) & S_TXD)) R(FIFO) = buf[k++]; }
        if (st & (S_ERR | S_CLKT)) break;
        if ((st & S_DONE) && k == n) break;
        for (volatile int j = 0; j < 50; j++) {}
    }
    R(S) = S_DONE | S_ERR | S_CLKT;
    if (st & S_ERR) return 2;
    if (st & S_CLKT) return 3;
    if (!(st & S_DONE)) return 4;
    return 0;
}

static u32 last_seq = 0;
static u8 buf[80] = { 1 };
static void serve(svc_page_t *sp) {
    u32 st = 0, rl = 0;
    switch (sp->op) {
    case 1: {
        u8 n = 0;
        for (u32 a = 0x08; a < 0x78 && n < 64; a++) if (xfer(a, 1, buf, 1) == 0) sp->rsp[1 + n++] = (u8)a;
        sp->rsp[0] = n; rl = 1 + n; break; }
    case 2: {
        if (sp->req_len < 3) { st = 1; break; }
        u8 addr = sp->req[0], reg = sp->req[1], n = sp->req[2];
        if (n < 1 || n > 64) { st = 1; break; }
        if (reg != 0xFF) { buf[0] = reg; if ((st = xfer(addr, 0, buf, 1))) break; }
        if ((st = xfer(addr, 1, sp->rsp, n))) break;
        rl = n; break; }
    case 3: {
        if (sp->req_len < 2 || sp->req_len > 65) { st = 1; break; }
        for (u32 i = 1; i < sp->req_len; i++) buf[i - 1] = sp->req[i];
        st = xfer(sp->req[0], 0, buf, sp->req_len - 1); break; }
    default: st = 1;
    }
    sp->rsp_len = rl; sp->status = st;
    __asm__ volatile("dsb sy" ::: "memory");
    sp->rsp_seq = sp->req_seq;
}

__attribute__((section(".text.main")))
u64 resident_main(const api_t *api, u64 op, u64 arg) {
    (void)arg;
    switch (op) {
    case RES_NAME: return (u64)"i2c";
    case RES_INIT: {
        A = api;
        pins_alt0();
        R(DIV) = 1500;                                 /* 150 MHz core clock / 1500 = 100 kHz */
        u8 b; int found = 0;
        for (u32 a = 0x08; a < 0x78; a++) if (xfer(a, 1, &b, 1) == 0) found++;
        static char m[] = "[I2C] BSC1 up, bus scan: 00 devices";   /* static: no memcpy/memset in a freestanding unit */
        m[25] = '0' + (found / 10) % 10; m[26] = '0' + found % 10;
        A->log(m);
        return 0; }
    case RES_TICK: {
        svc_page_t *sp = (svc_page_t *)UNIT_SVC_VA;
        if (sp->req_seq != last_seq) { last_seq = sp->req_seq; serve(sp); }
        return 0; }
    case RES_STOP: return 0;
    }
    return 0;
}
