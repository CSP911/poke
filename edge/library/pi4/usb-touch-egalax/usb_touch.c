/* ============================================
 * POKE resident driver — Pi 4 bare-metal USB host → eGalax HID touch
 *
 * Injected over the network (RSLD), never part of kernel8.img. Owns the
 * whole USB path: PCIe root complex bring-up, VL805 xHCI, the VL805 internal
 * hub, the downstream full-speed HID device, and the interrupt-IN polling
 * that turns reports into api->svc->touch. Every step was first proven with
 * EXEC probes (incubation) — see device.json for the register-level facts.
 *
 * Kernel contract: u64 resident_main(const api_t*, op, arg); see poke_api.h.
 * ============================================ */
typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef unsigned long u64;
#include "../../../kernel/pi4/poke_api.h"

static const api_t *A;                          /* kernel API, set on every call */

/* ── platform shims (mirror the kernel's helpers; residents bring their own) ── */
static inline void dsb(void) { __asm__ volatile("dsb sy" ::: "memory"); }
static u32  rd32(u64 a) { return *(volatile u32 *)a; }
static void wr32(u64 a, u32 v) { *(volatile u32 *)a = v; }
static u64  timer_cnt(void) { u64 v; __asm__ volatile("mrs %0, cntpct_el0":"=r"(v)); return v; }
static u64  timer_frq(void) { u64 v; __asm__ volatile("mrs %0, cntfrq_el0":"=r"(v)); return v; }
static void delay_us(u32 us) { u64 s = timer_cnt(); u64 t = (timer_frq() * us) / 1000000; while (timer_cnt() - s < t); }
static void delay_ms(u32 ms) { delay_us(ms * 1000); }
static int  scpy(char *d, const char *s) { int n=0; while (*s) d[n++]=*s++; return n; }
static int  idec(u32 v, char *b) { char t[12]; int i=0; if (!v){b[0]='0';return 1;} while(v){t[i++]='0'+(v%10);v/=10;} int l=i; for(int j=0;j<l;j++) b[j]=t[l-1-j]; return l; }
static int  clampi(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }
static void uprint(const char *s) { if (A && A->log) A->log(s); }
static void uputc(char c) { char b[2] = { c, 0 }; uprint(b); }
static void udec(u32 v) { char b[12]; int n = idec(v, b); b[n] = 0; uprint(b); }
static void uhex32(u32 v) { const char h[] = "0123456789abcdef"; char b[11] = "0x"; for (int i = 0; i < 8; i++) b[2+i] = h[(v >> (28 - i*4)) & 0xF]; b[10] = 0; uprint(b); }

/* display via the kernel (fb_* names kept so the incubated code reads unchanged) */
#define fb_ok   (A && A->screen && A->screen() != 0)
#define fb_w    (A->screen() >> 16)
#define fb_h    (A->screen() & 0xffff)
static void fb_text(int x, int y, int scale, u32 color, const char *s) { if (fb_ok) A->text(x, y, scale, color, s); }

/* VideoCore mailbox — the VL805 firmware reload goes through it */
#define PERI 0xFE000000ULL
#define MBOX (PERI + 0x00B880)
static u32 __attribute__((aligned(16))) mbox_buf[64];
static int mbox_call(void) {
    u32 bus = (u32)(u64)mbox_buf + 0xC0000000U;
    dsb();
    while (rd32(MBOX + 0x18) & 0x80000000);
    wr32(MBOX + 0x20, (bus & ~0xF) | 8);
    while (1) {
        while (rd32(MBOX + 0x18) & 0x40000000);
        u32 r = rd32(MBOX + 0x00);
        if ((r & 0xF) == 8) return (mbox_buf[1] & 0x80000000) != 0;
    }
}

/* touch state served to personas through api->svc->touch */
static int touch_down_f = 0, touch_new = 0, touch_sx = 0, touch_sy = 0;
static int res_touch(int *x, int *y) {
    if (x) *x = touch_sx;
    if (y) *y = touch_sy;
    if (!touch_down_f) return 0;
    if (touch_new) { touch_new = 0; return 2; }
    return 1;
}

/* ═══════════════════════════════════════════
 * Bare-metal USB: PCIe root complex + VL805 xHCI
 * Setup sequence proven via EXEC probes (see memory pi4-usb-pcie).
 * ═══════════════════════════════════════════ */
#define PCIE      0xFD500000ULL
#define XHCI      0x600000000ULL   /* CPU phys → outbound win → PCIe 0xF8000000 → VL805 BAR0 */
#define USB_DCBAA 0x02200000ULL    /* xHCI DMA structs (identity-mapped inbound) */
#define USB_CMDR  0x02201000ULL
#define USB_EVTR  0x02202000ULL
#define USB_ERST  0x02203000ULL
#define USB_SPAD_ARR 0x02208000ULL /* scratchpad buffer array */
#define USB_SPAD_BUF 0x02210000ULL /* scratchpad buffers (31 × 4KB → ...0x0222F000) */
#define RING_TRBS 16

static u32  prd(u32 o) { return rd32(PCIE + o); }
static void pwr(u32 o, u32 v) { wr32(PCIE + o, v); }
static void pwr8(u32 o, u8 v)  { *(volatile u8 *)(PCIE + o) = v; }
static void pwr16(u32 o, u16 v){ *(volatile u16 *)(PCIE + o) = v; }
static void pwrfld(u32 o, u32 m, u32 v) { u32 s=__builtin_ctz(m); u32 t=prd(o); t=(t&~m)|((v<<s)&m); pwr(o,t); (void)prd(o); }
static u32  vcrd(u32 reg) { pwr(0x9000, 0x100000); return rd32(PCIE + 0x8000 + reg); }
static void vcwr(u32 reg, u32 v) { pwr(0x9000, 0x100000); wr32(PCIE + 0x8000 + reg, v); }

static int pcie_init(void) {
    pwrfld(0x9210,0x2,1); pwrfld(0x9210,0x1,1); delay_us(200); pwrfld(0x9210,0x2,0);
    pwrfld(0x4204,0x08000000,0); delay_us(200);
    u32 mc=prd(0x4008); mc|=0x1000; mc|=0x2000; mc&=~0x300000; pwr(0x4008,mc);
    pwr(0x4034,0x11); pwr(0x4038,0); pwrfld(0x4008,0xf8000000,0x11);
    pwrfld(0x9210,0x1,0); delay_ms(100);
    int up=0; for(int k=0;k<30&&!up;k++){u32 st=prd(0x4068); up=((st&0x20)&&(st&0x10)); if(!up)delay_ms(5);}
    if(!up) return -1;
    pwrfld(0x043c,0xffffff,0x060400);
    pwr8(0x19,1); pwr8(0x1a,1); pwr16(0xac+0x1c,0x0010); (void)prd(0x18);
    pwr(0x400c,0xF8000000); pwr(0x4010,0);
    pwr(0x4070,0x00300000); pwr(0x4080,0x6); pwr(0x4084,0x6);
    pwr16(0x20,0xF800); pwr16(0x22,0xF800); pwr16(0x04,0x0006);
    vcwr(0x10,0xF8000004); vcwr(0x14,0); vcwr(0x04,0x0006);
    mbox_buf[0]=7*4; mbox_buf[1]=0; mbox_buf[2]=0x00030058; mbox_buf[3]=4; mbox_buf[4]=0; mbox_buf[5]=0x00100000; mbox_buf[6]=0;
    mbox_call(); delay_ms(300);
    return (vcrd(0)==0x34831106) ? 0 : -2;
}

static u32  xrd(u64 a) { return *(volatile u32 *)a; }
static void xwr(u64 a, u32 v) { *(volatile u32 *)a = v; }
static void xwr64(u64 a, u64 v) { *(volatile u32 *)a=(u32)v; *(volatile u32 *)(a+4)=(u32)(v>>32); }
static void xzero(u64 a, int n) { for(int i=0;i<n/4;i++) *(volatile u32 *)(a+i*4)=0; }

static u64 xhci_op, xhci_rt, xhci_db;
static int xhci_slots, xhci_ports, xhci_spad;

static int xhci_init(void) {
    int caplen = xrd(XHCI+0) & 0xFF;
    xhci_op = XHCI + caplen;
    u32 hcs1 = xrd(XHCI+4);
    xhci_slots = hcs1 & 0xFF;
    xhci_ports = (hcs1 >> 24) & 0xFF;
    xhci_db = XHCI + (xrd(XHCI+0x14) & ~3u);
    xhci_rt = XHCI + (xrd(XHCI+0x18) & ~0x1fu);
    int t;
    t=1000; while((xrd(xhci_op+0x04)&(1<<11))&&t--)delay_ms(1);      /* CNR */
    xwr(xhci_op+0x00, xrd(xhci_op+0x00)|(1<<1));                     /* HCRST */
    t=1000; while((xrd(xhci_op+0x00)&(1<<1))&&t--)delay_ms(1);
    t=1000; while((xrd(xhci_op+0x04)&(1<<11))&&t--)delay_ms(1);
    xwr(xhci_op+0x38, xhci_slots);                                  /* CONFIG MaxSlotsEn */
    xzero(USB_DCBAA,(xhci_slots+1)*8);
    /* Scratchpad buffers (HCSPARAMS2 Max Scratchpad Bufs) — controller
     * needs these before start or Address Device hangs the bus. */
    u32 hcs2 = xrd(XHCI+8);
    int nspb = (((hcs2>>21)&0x1f)<<5) | ((hcs2>>27)&0x1f);
    xhci_spad = nspb;
    if (nspb) {
        for (int i=0;i<nspb;i++) {
            u64 buf = USB_SPAD_BUF + (u64)i*0x1000;
            xzero(buf, 0x1000);
            xwr64(USB_SPAD_ARR + i*8, buf);
        }
        xwr64(USB_DCBAA + 0, USB_SPAD_ARR);   /* DCBAA[0] = scratchpad array */
    }
    xwr64(xhci_op+0x30, USB_DCBAA);                                 /* DCBAAP */
    xzero(USB_CMDR, RING_TRBS*16);                                  /* command ring + link TRB */
    xwr(USB_CMDR+(RING_TRBS-1)*16+0,(u32)USB_CMDR);
    xwr(USB_CMDR+(RING_TRBS-1)*16+12,(6<<10)|(1<<1)|1);             /* Link TRB, TC=1, C=1 */
    xwr64(xhci_op+0x18, USB_CMDR|1);                               /* CRCR, RCS=1 */
    xzero(USB_EVTR, RING_TRBS*16);                                  /* event ring + ERST */
    xzero(USB_ERST, 16);
    xwr(USB_ERST+0,(u32)USB_EVTR); xwr(USB_ERST+8, RING_TRBS);
    xwr(xhci_rt+0x20+0x08, 1);                                      /* ERSTSZ */
    xwr64(xhci_rt+0x20+0x10, USB_ERST);                            /* ERSTBA */
    xwr64(xhci_rt+0x20+0x18, USB_EVTR);                            /* ERDP */
    xwr(xhci_op+0x00, xrd(xhci_op+0x00)|1);                        /* R/S start */
    t=1000; while((xrd(xhci_op+0x04)&1)&&t--)delay_ms(1);          /* wait HCH clear */
    return (xrd(xhci_op+0x04)&1) ? -1 : 0;
}

static void usb_init(void) {
    uprint("[USB] PCIe bring-up...\n");
    if (pcie_init()) { uprint("[USB] PCIe FAILED\n"); if(fb_ok) fb_text(40,520,3,0x00FF4040,"USB: PCIe failed"); return; }
    uprint("[USB] VL805 enumerated (xHCI). init...\n");
    if (xhci_init()) { uprint("[USB] xHCI start FAILED\n"); if(fb_ok) fb_text(40,520,3,0x00FF4040,"USB: xHCI start failed"); return; }
    char b[96]; int n=scpy(b,"USB xHCI running  slots="); n+=idec(xhci_slots,b+n); n+=scpy(b+n," ports="); n+=idec(xhci_ports,b+n); n+=scpy(b+n," spad="); n+=idec(xhci_spad,b+n); b[n]=0;
    uprint("[USB] "); uprint(b); uputc('\n');
    if(fb_ok) fb_text(40,520,2,0x0000FF66,b);
    int y=548;
    for (int p=1; p<=xhci_ports; p++) {
        u32 sc = xrd(xhci_op + 0x400 + (p-1)*0x10);
        char c[48]; int m=scpy(c,"port "); m+=idec(p,c+m); m+=scpy(c+m,(sc&1)?" CONNECTED":" -"); c[m]=0;
        uprint("[USB] "); uprint(c); uprint(" sc="); uhex32(sc); uputc('\n');
        if((sc&1)&&fb_ok){ fb_text(40,y,2,0x00FFFF00,c); y+=28; }
    }
}

/* ═══════════════════════════════════════════
 * USB enumeration: root port → (hub → downstream port) → HID touch.
 * Sequence proven step by step with EXEC probes; every wait is bounded so a
 * missing/odd device can never hang boot — we just come up without touch.
 * Lessons baked in (see memory pi4-usb-pcie):
 *  - event ring wraps at RING_TRBS with cycle toggle, ERDP advanced on read
 *  - hub slot must carry Hub bit / NbrPorts / TTT (Configure Endpoint)
 *  - single-TT hub: child TT Port Number = 0 (else Parameter Error)
 *  - child EP0 MPS learned from the first 8 descriptor bytes (Evaluate Ctx)
 *  - ~50ms settle after Address Device on FS devices
 *  - never SET_IDLE (some HIDs STALL → EP0 halts, TT wedges)
 * ═══════════════════════════════════════════ */
#define USB_INCTX   0x02204000ULL   /* hub: input ctx / device ctx / EP0 ring (4KB) / data */
#define USB_DEVCTX  0x02205000ULL
#define USB_EP0R    0x02206000ULL
#define USB_DBUF    0x02207000ULL
#define USB_INCTX2  0x02209000ULL   /* touch device: same set */
#define USB_DEVCTX2 0x0220a000ULL
#define USB_EP0R2   0x0220b000ULL
#define USB_DBUF2   0x0220c000ULL
#define USB_EPIR    0x0220d000ULL   /* interrupt IN ring: 15 TRBs + Link */
#define USB_RBUF    0x0220e000ULL   /* 15 × 64B report buffers */
#define USB_IR_TRBS 15
#define USB_ERDP    (xhci_rt + 0x20 + 0x18)

static int usb_ci = 0, usb_cc = 1;      /* command ring enqueue index / cycle */
static int usb_ei = 0, usb_ec = 1;      /* event ring dequeue index / cycle */
static int usb_ir_pi = 0, usb_ir_pc = 1;/* interrupt ring producer */
static int usb_touch_ok = 0, usb_touch_slot = 0, usb_touch_dci = 0;
static u32 usb_touch_vid = 0, usb_touch_pid = 0;
static u32 usb_touch_reports = 0, usb_touch_errs = 0;

static u32 xrb(u64 a) { return *(volatile u8 *)a; }
static int uhex16s(u32 v, char *b) { const char h[] = "0123456789abcdef"; for (int i = 0; i < 4; i++) b[i] = h[(v >> (12 - i*4)) & 0xF]; return 4; }

/* Wait for an event of type `want`; other events are consumed and dropped. */
static int usb_event(int want, u32 *l0, u32 *l2, u32 *l3, int tmo_ms) {
    for (int s = 0; s < tmo_ms; s++) {
        u32 c = xrd(USB_EVTR + usb_ei*16 + 12);
        if ((c & 1) == (u32)usb_ec) {
            if (l0) *l0 = xrd(USB_EVTR + usb_ei*16);
            if (l2) *l2 = xrd(USB_EVTR + usb_ei*16 + 8);
            if (l3) *l3 = c;
            usb_ei++; if (usb_ei == RING_TRBS) { usb_ei = 0; usb_ec ^= 1; }
            xwr64(USB_ERDP, (USB_EVTR + usb_ei*16) | 8);
            if (((c >> 10) & 0x3f) == want) return 1;
            continue;
        }
        delay_ms(1);
    }
    return 0;
}

/* Queue one command TRB, ring doorbell 0, return completion code (-1 timeout). */
static int usb_cmd(u64 ptr, u32 d3, u32 *ev3) {
    u64 t = USB_CMDR + usb_ci*16;
    xwr(t+0, (u32)ptr); xwr(t+4, (u32)(ptr >> 32)); xwr(t+8, 0); xwr(t+12, (d3 & ~1u) | (u32)usb_cc);
    usb_ci++;
    if (usb_ci == RING_TRBS-1) {            /* Link TRB at the end: give it our cycle, wrap */
        xwr(USB_CMDR + (RING_TRBS-1)*16 + 12, (6<<10) | (1<<1) | (u32)usb_cc);
        usb_ci = 0; usb_cc ^= 1;
    }
    dsb(); xwr(xhci_db, 0);
    u32 l2 = 0, l3 = 0;
    if (!usb_event(33, 0, &l2, &l3, 1000)) return -1;
    if (ev3) *ev3 = l3;
    return (l2 >> 24) & 0xff;
}

/* Control transfer on a slot's EP0 ring (no Link TRB: 4KB ring, boot-only use). */
static int usb_ctrl(int slot, u64 ring, int *e, u32 sd0, u32 sd1, u64 buf, int dlen, int din) {
    if (*e > 240) return -2;
    u64 b = ring + (*e)*16; int trt = dlen ? (din ? 3 : 2) : 0;
    xwr(b+0, sd0); xwr(b+4, sd1); xwr(b+8, 8); xwr(b+12, (trt<<16)|(2<<10)|(1<<6)|1); (*e)++;
    if (dlen > 0) { b = ring + (*e)*16; xwr(b+0, (u32)buf); xwr(b+4, 0); xwr(b+8, dlen); xwr(b+12, (3<<10)|((din?1:0)<<16)|1); (*e)++; }
    int sdir = dlen ? (din ? 0 : 1) : 1;
    b = ring + (*e)*16; xwr(b+0, 0); xwr(b+4, 0); xwr(b+8, 0); xwr(b+12, (4<<10)|(sdir<<16)|(1<<5)|1); (*e)++;
    dsb(); xwr(xhci_db + slot*4, 1);
    u32 l2 = 0;
    if (!usb_event(32, 0, &l2, 0, 1000)) return -1;
    return (l2 >> 24) & 0xff;
}

static int usb_enable_slot(void) {
    u32 ev3 = 0;
    if (usb_cmd(0, (9<<10), &ev3) != 1) return 0;
    return (ev3 >> 24) & 0xff;
}

/* Address Device with the given slot context dwords + EP0 max packet size. */
static int usb_address(int slot, u64 inctx, u64 devctx, u64 ep0r, u32 s0, u32 s1, u32 s2, u32 mps) {
    xzero(inctx, 2048); xzero(devctx, 2048); xzero(ep0r, 4096);
    xwr(inctx+0x04, 0x3);
    xwr(inctx+0x20, s0); xwr(inctx+0x24, s1); xwr(inctx+0x28, s2);
    xwr(inctx+0x44, (mps<<16)|(4<<3)|(3<<1)); xwr64(inctx+0x48, ep0r|1); xwr(inctx+0x50, 8);
    xwr64(USB_DCBAA + slot*8, devctx); dsb();
    return usb_cmd(inctx, (slot<<24)|(11<<10), 0);
}

static void usb_ir_enqueue(void) {
    u64 t = USB_EPIR + usb_ir_pi*16;
    xwr(t+0, (u32)(USB_RBUF + usb_ir_pi*64)); xwr(t+4, 0); xwr(t+8, 64);
    xwr(t+12, (1u<<10)|(1u<<5)|(1u<<2)|(u32)usb_ir_pc);
    usb_ir_pi++;
    if (usb_ir_pi == USB_IR_TRBS) {
        u64 L = USB_EPIR + USB_IR_TRBS*16;
        xwr(L+0, (u32)USB_EPIR); xwr(L+4, 0); xwr(L+8, 0); xwr(L+12, (6u<<10)|(1u<<1)|(u32)usb_ir_pc);
        usb_ir_pi = 0; usb_ir_pc ^= 1;
    }
}

static void usb_status(const char *s, u32 color) {
    uprint("[USB] "); uprint(s); uputc('\n');
    if (fb_ok) fb_text(40, 576, 2, color, s);
}

static void usb_enumerate(void) {
    if (!xhci_op) return;
    /* 1. first connected root port */
    int rp = 0;
    for (int p = 1; p <= xhci_ports; p++) if (xrd(xhci_op + 0x400 + (p-1)*0x10) & 1) { rp = p; break; }
    if (!rp) { usb_status("USB: nothing on root ports", 0x00FFFF00); return; }
    u64 psc = xhci_op + 0x400 + (rp-1)*0x10;
    xwr(psc, (xrd(psc) & ~0x00fe0000u) | 0x10); delay_ms(120);          /* port reset */
    u32 sc = xrd(psc); int rspd = (sc >> 10) & 0xf;
    if (!(sc & 2)) { usb_status("USB: root port reset failed", 0x00FF4040); return; }

    /* 2. address whatever sits on the root port */
    int s1 = usb_enable_slot(); if (!s1) { usb_status("USB: enable slot failed", 0x00FF4040); return; }
    if (usb_address(s1, USB_INCTX, USB_DEVCTX, USB_EP0R, (1u<<27)|((u32)rspd<<20), (u32)rp<<16, 0, rspd==3?64:8) != 1) {
        usb_status("USB: address (root) failed", 0x00FF4040); return; }
    int e1 = 0; delay_ms(50);
    xzero(USB_DBUF, 64);                                                /* 8 bytes: class/proto, safe for any MPS0 */
    if (usb_ctrl(s1, USB_EP0R, &e1, 0x80|(0x06<<8)|(0x0100<<16), 8<<16, USB_DBUF, 8, 1) != 1) {
        usb_status("USB: root descriptor failed", 0x00FF4040); return; }
    int cls = xrb(USB_DBUF+4), proto = xrb(USB_DBUF+6);

    int dev = s1, dport = 0, dspd = rspd; u64 inctx = USB_INCTX, devctx = USB_DEVCTX, ep0r = USB_EP0R, dbuf = USB_DBUF;
    int *ep = &e1; int e2 = 0;
    if (cls == 9) {
        /* 3. hub: configure, mark as hub, power ports, find the device */
        int hub = s1, mtt = (proto == 2);
        if (usb_ctrl(hub, USB_EP0R, &e1, 0x00|(0x09<<8)|(1<<16), 0, 0, 0, 0) != 1) { usb_status("USB: hub set_config failed", 0x00FF4040); return; }
        xzero(USB_DBUF, 32);
        if (usb_ctrl(hub, USB_EP0R, &e1, 0xA0|(0x06<<8)|(0x2900<<16), 16<<16, USB_DBUF, 16, 1) != 1) { usb_status("USB: hub descriptor failed", 0x00FF4040); return; }
        int nports = xrb(USB_DBUF+2); u32 ttt = (xrb(USB_DBUF+3) >> 5) & 3;
        xzero(USB_INCTX, 2048); xwr(USB_INCTX+0x04, 0x1);
        xwr(USB_INCTX+0x20, (1u<<27)|((u32)rspd<<20)|(1u<<26)|(mtt?(1u<<25):0));
        xwr(USB_INCTX+0x24, ((u32)rp<<16)|((u32)nports<<24)); xwr(USB_INCTX+0x28, ttt<<16); dsb();
        if (usb_cmd(USB_INCTX, (hub<<24)|(12<<10), 0) != 1) { usb_status("USB: hub configure failed", 0x00FF4040); return; }
        for (int p = 1; p <= nports; p++) usb_ctrl(hub, USB_EP0R, &e1, 0x23|(0x03<<8)|(8<<16), p, 0, 0, 0);
        delay_ms(150);
        for (int p = 1; p <= nports && !dport; p++) {
            xzero(USB_DBUF, 8);
            if (usb_ctrl(hub, USB_EP0R, &e1, 0xA3, p|(4<<16), USB_DBUF, 4, 1) == 1 && (xrd(USB_DBUF) & 1)) dport = p;
        }
        if (!dport) { usb_status("USB: hub up, no device on its ports", 0x00FFFF00); return; }
        usb_ctrl(hub, USB_EP0R, &e1, 0x23|(0x03<<8)|(4<<16), dport, 0, 0, 0); delay_ms(60);   /* PORT_RESET */
        xzero(USB_DBUF, 8); usb_ctrl(hub, USB_EP0R, &e1, 0xA3, dport|(4<<16), USB_DBUF, 4, 1);
        u32 ps = xrd(USB_DBUF);
        dspd = (ps & (1<<9)) ? 2 : (ps & (1<<10)) ? 3 : 1;
        usb_ctrl(hub, USB_EP0R, &e1, 0x23|(0x01<<8)|(16<<16), dport, 0, 0, 0);
        usb_ctrl(hub, USB_EP0R, &e1, 0x23|(0x01<<8)|(20<<16), dport, 0, 0, 0);
        dev = usb_enable_slot(); if (!dev) { usb_status("USB: enable slot (dev) failed", 0x00FF4040); return; }
        inctx = USB_INCTX2; devctx = USB_DEVCTX2; ep0r = USB_EP0R2; dbuf = USB_DBUF2; ep = &e2;
        u32 tt = (dspd == 3) ? 0 : ((u32)hub | (mtt ? ((u32)dport<<8) : 0));   /* single-TT: port 0 */
        if (usb_address(dev, inctx, devctx, ep0r, (1u<<27)|((u32)dspd<<20)|(u32)dport, (u32)rp<<16, tt, dspd==3?64:8) != 1) {
            usb_status("USB: address (dev) failed", 0x00FF4040); return; }
        delay_ms(50);
    }

    /* 4. device descriptor: learn EP0 MPS, then the full 18 bytes */
    xzero(dbuf, 64);
    if (usb_ctrl(dev, ep0r, ep, 0x80|(0x06<<8)|(0x0100<<16), 8<<16, dbuf, 8, 1) != 1) { usb_status("USB: dev descriptor(8) failed", 0x00FF4040); return; }
    u32 mps0 = xrb(dbuf+7);
    if (mps0 != (u32)(dspd==3?64:8) && mps0 >= 8) {                     /* Evaluate Context: EP0 MPS */
        xzero(inctx, 2048); xwr(inctx+0x04, 0x2);
        xwr(inctx+0x44, (mps0<<16)|(4<<3)|(3<<1)); dsb();
        if (usb_cmd(inctx, (dev<<24)|(13<<10), 0) != 1) { usb_status("USB: evaluate ctx failed", 0x00FF4040); return; }
    }
    if (usb_ctrl(dev, ep0r, ep, 0x80|(0x06<<8)|(0x0100<<16), 18<<16, dbuf, 18, 1) != 1) { usb_status("USB: dev descriptor failed", 0x00FF4040); return; }
    usb_touch_vid = xrd(dbuf+8) & 0xffff; usb_touch_pid = (xrd(dbuf+8) >> 16) & 0xffff;

    /* 5. config descriptor → HID interface + interrupt IN endpoint */
    xzero(dbuf, 16);
    if (usb_ctrl(dev, ep0r, ep, 0x80|(0x06<<8)|(0x0200<<16), 9<<16, dbuf, 9, 1) != 1) { usb_status("USB: config(9) failed", 0x00FF4040); return; }
    u32 wtot = xrb(dbuf+2) | (xrb(dbuf+3) << 8); if (wtot < 9 || wtot > 1024) wtot = 9;
    xzero(dbuf, wtot + 16);
    if (usb_ctrl(dev, ep0r, ep, 0x80|(0x06<<8)|(0x0200<<16), wtot<<16, dbuf, (int)wtot, 1) != 1) { usb_status("USB: config descriptor failed", 0x00FF4040); return; }
    u32 cfgv = xrb(dbuf+5); int ifcls = -1, epa = -1, epmps = 64, epiv = 3;
    for (u32 o = 0; o + 2 <= wtot; ) {
        u32 l = xrb(dbuf+o), ty = xrb(dbuf+o+1); if (!l) break;
        if (ty == 4 && ifcls < 0) ifcls = xrb(dbuf+o+5);
        else if (ty == 5 && epa < 0) { u32 a = xrb(dbuf+o+2), at = xrb(dbuf+o+3);
            if ((a & 0x80) && (at & 3) == 3) { epa = a; epmps = xrb(dbuf+o+4) | (xrb(dbuf+o+5)<<8); epiv = xrb(dbuf+o+6); } }
        o += l;
    }
    if (ifcls != 3 || epa < 0) { usb_status("USB: device is not a HID with interrupt IN", 0x00FFFF00); return; }

    /* 6. Configure Endpoint (interrupt IN), SET_CONFIGURATION, prime the ring */
    int dci = ((epa & 0xf) * 2) + 1;
    u32 interval;                                                   /* xHCI: 2^interval × 125µs */
    if (dspd == 3) { interval = epiv ? epiv - 1 : 0; }                /* HS: bInterval is 2^(n-1) µframes */
    else { u32 f = 0; while ((2u << f) <= (epiv ? epiv : 1)) f++; interval = f + 3; }   /* FS/LS: ms → µframes */
    if (interval > 15) interval = 15;
    xzero(inctx, 2048); xzero(USB_EPIR, 4096);
    xwr(inctx+0x04, 1u | (1u << dci));
    xwr(inctx+0x20, ((u32)dci<<27) | ((u32)dspd<<20) | (u32)dport);
    xwr(inctx+0x24, (u32)rp<<16);
    xwr(inctx+0x28, (dspd == 3) ? 0 : (xrd(devctx+8) & 0xffff));     /* keep TT fields from the addressed slot */
    u64 ec = inctx + 0x20 + (u64)dci*0x20;
    xwr(ec+0x00, interval<<16);
    xwr(ec+0x04, ((u32)epmps<<16)|(7u<<3)|(3u<<1));
    xwr64(ec+0x08, USB_EPIR|1);
    xwr(ec+0x10, ((u32)epmps<<16)|(u32)epmps);
    dsb();
    if (usb_cmd(inctx, (dev<<24)|(12<<10), 0) != 1) { usb_status("USB: configure endpoint failed", 0x00FF4040); return; }
    if (usb_ctrl(dev, ep0r, ep, 0x00|(0x09<<8)|(cfgv<<16), 0, 0, 0, 0) != 1) { usb_status("USB: set_configuration failed", 0x00FF4040); return; }
    delay_ms(50);
    xzero(USB_RBUF, USB_IR_TRBS*64);
    usb_ir_pi = 0; usb_ir_pc = 1;
    for (int i = 0; i < 8; i++) usb_ir_enqueue();
    dsb(); xwr(xhci_db + dev*4, (u32)dci);
    usb_touch_slot = dev; usb_touch_dci = dci; usb_touch_ok = 1;

    char b[96]; int n = scpy(b, "USB touch: "); n += scpy(b+n, dspd==3?"HS":dspd==2?"LS":"FS");
    n += scpy(b+n, " HID "); n += uhex16s(usb_touch_vid, b+n); b[n++] = ':'; n += uhex16s(usb_touch_pid, b+n);
    if (dport) { n += scpy(b+n, " via hub port "); n += idec(dport, b+n); }
    b[n] = 0;
    usb_status(b, 0x0000FF66);
}

/* Main-loop poll: drain the event ring, turn touch reports into touch_* state.
 * Report layout (eGalax 0eef:0005, measured): [0]=id [1]=tip [4..5]=X [6..7]=Y,
 * both already in panel pixels (0..1023 / 0..599). */
static void usb_touch_poll(void) {
    if (!usb_touch_ok) return;
    for (int k = 0; k < 16; k++) {
        u32 c = xrd(USB_EVTR + usb_ei*16 + 12);
        if ((c & 1) != (u32)usb_ec) break;
        u32 t0 = xrd(USB_EVTR + usb_ei*16), t2 = xrd(USB_EVTR + usb_ei*16 + 8);
        usb_ei++; if (usb_ei == RING_TRBS) { usb_ei = 0; usb_ec ^= 1; }
        xwr64(USB_ERDP, (USB_EVTR + usb_ei*16) | 8);
        if (((c >> 10) & 0x3f) != 32 || (int)((c >> 16) & 0x1f) != usb_touch_dci) continue;
        u32 cc = (t2 >> 24) & 0xff; int i = (int)((t0 - (u32)USB_EPIR) / 16);
        if ((cc == 1 || cc == 13) && i >= 0 && i < USB_IR_TRBS) {
            u64 r = USB_RBUF + (u64)i*64;
            int tip = xrb(r+1) & 1;
            int x = (int)(xrb(r+4) | (xrb(r+5) << 8)), y = (int)(xrb(r+6) | (xrb(r+7) << 8));
            usb_touch_reports++;
            if (tip) {
                touch_sx = clampi(x, 0, (int)fb_w - 1); touch_sy = clampi(y, 0, (int)fb_h - 1);
                if (!touch_down_f) { touch_down_f = 1; touch_new = 1;
                    uprint("[TOUCH] down "); udec(touch_sx); uputc(','); udec(touch_sy); uputc('\n'); }
            } else if (touch_down_f) { touch_down_f = 0; uprint("[TOUCH] up\n"); }
            usb_ir_enqueue(); dsb(); xwr(xhci_db + usb_touch_slot*4, (u32)usb_touch_dci);
        } else {
            /* endpoint halted (stall / split error): reset it and re-arm the ring */
            usb_touch_errs++;
            usb_cmd(0, (usb_touch_slot<<24)|(usb_touch_dci<<16)|(14<<10), 0);                       /* Reset Endpoint */
            usb_cmd((USB_EPIR + usb_ir_pi*16) | (u32)usb_ir_pc, (usb_touch_slot<<24)|(usb_touch_dci<<16)|(16<<10), 0); /* Set TR Dequeue */
            usb_ir_enqueue(); dsb(); xwr(xhci_db + usb_touch_slot*4, (u32)usb_touch_dci);
        }
    }
}


/* ── resident entry (offset 0) ── */
__attribute__((section(".text.main")))
u64 resident_main(const api_t *api, u64 op, u64 arg) {
    (void)arg; A = api;
    switch (op) {
    case RES_NAME: return (u64)"usb-touch-egalax";
    case RES_INIT:
        usb_init();
        usb_enumerate();
        if (!usb_touch_ok) return 1;
        api->svc->touch = res_touch;
        return 0;
    case RES_TICK: usb_touch_poll(); return 0;
    case RES_STOP:
        if (xhci_op) xwr(xhci_op + 0x00, xrd(xhci_op + 0x00) & ~1u);   /* R/S = 0: controller stops all DMA */
        usb_touch_ok = 0; touch_down_f = 0;
        return 0;
    }
    return 0;
}
