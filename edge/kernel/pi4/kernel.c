/* ============================================
 * POKE OS — Raspberry Pi 4 Bare-Metal Ethernet
 *
 * BCM2711 (Cortex-A72) | GENET v5 | UDP
 * Real GPIO + Real SoC temperature (mailbox)
 *
 * Network: static IP 10.0.0.2, UDP port 5555
 * ============================================ */

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long u64;

/* ── MMIO ── */
static inline u32 rd32(u64 a) { return *(volatile u32 *)a; }
static inline void wr32(u64 a, u32 v) { *(volatile u32 *)a = v; }
static inline void dsb(void) { __asm__ volatile("dsb sy" ::: "memory"); }

/* ── ARM Generic Timer ── */
static u64 timer_cnt(void) { u64 v; __asm__ volatile("mrs %0, cntpct_el0":"=r"(v)); return v; }
static u64 timer_frq(void) { u64 v; __asm__ volatile("mrs %0, cntfrq_el0":"=r"(v)); return v; }
static void delay_us(u32 us) { u64 s = timer_cnt(); u64 t = (timer_frq() * us) / 1000000; while (timer_cnt() - s < t); }
static void delay_ms(u32 ms) { delay_us(ms * 1000); }

/* ── BCM2711 Peripherals ── */
#define PERI   0xFE000000ULL
#define GPIO   (PERI + 0x200000)
#define UART0  (PERI + 0x201000)
#define MBOX   (PERI + 0x00B880)
#define GENET  0xFD580000ULL

/* ── UART (PL011 — debug console) ── */
static void uart_init(void) {
    wr32(UART0 + 0x30, 0);
    u32 sel = rd32(GPIO + 0x04);
    sel &= ~(7 << 12); sel |= (4 << 12);
    sel &= ~(7 << 15); sel |= (4 << 15);
    wr32(GPIO + 0x04, sel);
    wr32(GPIO + 0xE4, rd32(GPIO + 0xE4) & ~(0xFU << 28));
    wr32(UART0 + 0x44, 0x7FF);
    wr32(UART0 + 0x24, 26);
    wr32(UART0 + 0x28, 3);
    wr32(UART0 + 0x2C, (3 << 5) | (1 << 4));
    wr32(UART0 + 0x30, (1 << 0) | (1 << 8) | (1 << 9));
}

static int fb_ok = 0;
static int persona_active = 0;
static int mmu_on;
static void fb_putc(char c);
static void uputc(char c) {
    int t = 100000;
    while ((rd32(UART0 + 0x18) & (1 << 5)) && --t) { }  /* bounded: never hang on UART */
    wr32(UART0, c);
    if (fb_ok && !persona_active) fb_putc(c);
}
static void uprint(const char *s) { while (*s) { if (*s == '\n') uputc('\r'); uputc(*s++); } }
static void uhex32(u32 v) { const char h[] = "0123456789abcdef"; uprint("0x"); for (int i = 28; i >= 0; i -= 4) uputc(h[(v >> i) & 0xF]); }
static void udec(u32 v) { char b[12]; int i = 0; if (!v) { uputc('0'); return; } while (v) { b[i++] = '0' + (v % 10); v /= 10; } while (i) uputc(b[--i]); }
static void uip(u32 ip) { udec((ip>>24)&0xFF); uputc('.'); udec((ip>>16)&0xFF); uputc('.'); udec((ip>>8)&0xFF); uputc('.'); udec(ip&0xFF); }

/* ── Memory / String ── */
static void mcpy(void *d, const void *s, int n) { u8 *a=d; const u8 *b=s; while (n-->0) *a++=*b++; }
static void mset(void *d, u8 v, int n) { u8 *a=d; while (n-->0) *a++=v; }
static int mcmp(const void *a, const void *b, int n) { const u8 *p=a,*q=b; while (n-->0) { if (*p!=*q) return *p-*q; p++; q++; } return 0; }
static int slen(const char *s) { int n=0; while (*s++) n++; return n; }
static int scpy(char *d, const char *s) { int n=0; while (*s) d[n++]=*s++; return n; }
static int idec(u32 v, char *b) { char t[12]; int i=0; if (!v){b[0]='0';return 1;} while(v){t[i++]='0'+(v%10);v/=10;} int l=i; for(int j=0;j<l;j++) b[j]=t[l-1-j]; return l; }

/* ── Byte Order / Checksum ── */
static inline u16 htons(u16 v) { return (v>>8)|(v<<8); }
static inline u16 ntohs(u16 v) { return htons(v); }
static inline u32 htonl(u32 v) { return ((v>>24)&0xFF)|((v>>8)&0xFF00)|((v<<8)&0xFF0000)|((v<<24)&0xFF000000U); }
static inline u32 ntohl(u32 v) { return htonl(v); }

static u16 ip_cksum(const void *data, int len) {
    const u16 *p = data; u32 sum = 0;
    while (len > 1) { sum += *p++; len -= 2; }
    if (len) sum += *(const u8 *)p;
    while (sum >> 16) sum = (sum & 0xFFFF) + (sum >> 16);
    return ~sum;
}

/* ── VideoCore Mailbox (for SoC temp) ── */
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

static u32 get_soc_temp(void) {
    mbox_buf[0] = 8 * 4;
    mbox_buf[1] = 0;
    mbox_buf[2] = 0x00030006;
    mbox_buf[3] = 8;
    mbox_buf[4] = 0;
    mbox_buf[5] = 0;
    mbox_buf[6] = 0;
    mbox_buf[7] = 0;
    dsb();
    if (mbox_call()) return mbox_buf[6];
    return 0;
}

/* ── Framebuffer Console (HDMI/DSI) ── */
static volatile u32 *fb_base;
static u32 fb_pitch, fb_w, fb_h;
static int fb_cx, fb_cy;

static const u8 font8x8[96][8] = {
    {0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00}, // space
    {0x18,0x3C,0x3C,0x18,0x18,0x00,0x18,0x00}, // !
    {0x6C,0x6C,0x00,0x00,0x00,0x00,0x00,0x00}, // "
    {0x6C,0x6C,0xFE,0x6C,0xFE,0x6C,0x6C,0x00}, // #
    {0x18,0x7E,0xC0,0x7C,0x06,0xFC,0x18,0x00}, // $
    {0x00,0xC6,0xCC,0x18,0x30,0x66,0xC6,0x00}, // %
    {0x38,0x6C,0x38,0x76,0xDC,0xCC,0x76,0x00}, // &
    {0x18,0x18,0x30,0x00,0x00,0x00,0x00,0x00}, // '
    {0x0C,0x18,0x30,0x30,0x30,0x18,0x0C,0x00}, // (
    {0x30,0x18,0x0C,0x0C,0x0C,0x18,0x30,0x00}, // )
    {0x00,0x66,0x3C,0xFF,0x3C,0x66,0x00,0x00}, // *
    {0x00,0x18,0x18,0x7E,0x18,0x18,0x00,0x00}, // +
    {0x00,0x00,0x00,0x00,0x00,0x18,0x18,0x30}, // ,
    {0x00,0x00,0x00,0x7E,0x00,0x00,0x00,0x00}, // -
    {0x00,0x00,0x00,0x00,0x00,0x18,0x18,0x00}, // .
    {0x06,0x0C,0x18,0x30,0x60,0xC0,0x80,0x00}, // /
    {0x7C,0xC6,0xCE,0xDE,0xF6,0xE6,0x7C,0x00}, // 0
    {0x18,0x38,0x78,0x18,0x18,0x18,0x7E,0x00}, // 1
    {0x7C,0xC6,0x06,0x1C,0x30,0x66,0xFE,0x00}, // 2
    {0x7C,0xC6,0x06,0x3C,0x06,0xC6,0x7C,0x00}, // 3
    {0x1C,0x3C,0x6C,0xCC,0xFE,0x0C,0x1E,0x00}, // 4
    {0xFE,0xC0,0xFC,0x06,0x06,0xC6,0x7C,0x00}, // 5
    {0x38,0x60,0xC0,0xFC,0xC6,0xC6,0x7C,0x00}, // 6
    {0xFE,0xC6,0x0C,0x18,0x30,0x30,0x30,0x00}, // 7
    {0x7C,0xC6,0xC6,0x7C,0xC6,0xC6,0x7C,0x00}, // 8
    {0x7C,0xC6,0xC6,0x7E,0x06,0x0C,0x78,0x00}, // 9
    {0x00,0x18,0x18,0x00,0x00,0x18,0x18,0x00}, // :
    {0x00,0x18,0x18,0x00,0x00,0x18,0x18,0x30}, // ;
    {0x0C,0x18,0x30,0x60,0x30,0x18,0x0C,0x00}, // <
    {0x00,0x00,0x7E,0x00,0x7E,0x00,0x00,0x00}, // =
    {0x60,0x30,0x18,0x0C,0x18,0x30,0x60,0x00}, // >
    {0x7C,0xC6,0x0C,0x18,0x18,0x00,0x18,0x00}, // ?
    {0x7C,0xC6,0xDE,0xDE,0xDE,0xC0,0x78,0x00}, // @
    {0x38,0x6C,0xC6,0xC6,0xFE,0xC6,0xC6,0x00}, // A
    {0xFC,0x66,0x66,0x7C,0x66,0x66,0xFC,0x00}, // B
    {0x3C,0x66,0xC0,0xC0,0xC0,0x66,0x3C,0x00}, // C
    {0xF8,0x6C,0x66,0x66,0x66,0x6C,0xF8,0x00}, // D
    {0xFE,0x62,0x68,0x78,0x68,0x62,0xFE,0x00}, // E
    {0xFE,0x62,0x68,0x78,0x68,0x60,0xF0,0x00}, // F
    {0x3C,0x66,0xC0,0xC0,0xCE,0x66,0x3E,0x00}, // G
    {0xC6,0xC6,0xC6,0xFE,0xC6,0xC6,0xC6,0x00}, // H
    {0x3C,0x18,0x18,0x18,0x18,0x18,0x3C,0x00}, // I
    {0x1E,0x0C,0x0C,0x0C,0xCC,0xCC,0x78,0x00}, // J
    {0xE6,0x66,0x6C,0x78,0x6C,0x66,0xE6,0x00}, // K
    {0xF0,0x60,0x60,0x60,0x62,0x66,0xFE,0x00}, // L
    {0xC6,0xEE,0xFE,0xFE,0xD6,0xC6,0xC6,0x00}, // M
    {0xC6,0xE6,0xF6,0xDE,0xCE,0xC6,0xC6,0x00}, // N
    {0x7C,0xC6,0xC6,0xC6,0xC6,0xC6,0x7C,0x00}, // O
    {0xFC,0x66,0x66,0x7C,0x60,0x60,0xF0,0x00}, // P
    {0x7C,0xC6,0xC6,0xC6,0xD6,0xDE,0x7C,0x0E}, // Q
    {0xFC,0x66,0x66,0x7C,0x6C,0x66,0xE6,0x00}, // R
    {0x7C,0xC6,0xC0,0x7C,0x06,0xC6,0x7C,0x00}, // S
    {0x7E,0x5A,0x18,0x18,0x18,0x18,0x3C,0x00}, // T
    {0xC6,0xC6,0xC6,0xC6,0xC6,0xC6,0x7C,0x00}, // U
    {0xC6,0xC6,0xC6,0xC6,0x6C,0x38,0x10,0x00}, // V
    {0xC6,0xC6,0xD6,0xFE,0xFE,0xEE,0xC6,0x00}, // W
    {0xC6,0xC6,0x6C,0x38,0x6C,0xC6,0xC6,0x00}, // X
    {0x66,0x66,0x66,0x3C,0x18,0x18,0x3C,0x00}, // Y
    {0xFE,0xC6,0x8C,0x18,0x32,0x66,0xFE,0x00}, // Z
    {0x3C,0x30,0x30,0x30,0x30,0x30,0x3C,0x00}, // [
    {0xC0,0x60,0x30,0x18,0x0C,0x06,0x02,0x00}, // backslash
    {0x3C,0x0C,0x0C,0x0C,0x0C,0x0C,0x3C,0x00}, // ]
    {0x10,0x38,0x6C,0xC6,0x00,0x00,0x00,0x00}, // ^
    {0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xFF}, // _
    {0x30,0x18,0x0C,0x00,0x00,0x00,0x00,0x00}, // `
    {0x00,0x00,0x78,0x0C,0x7C,0xCC,0x76,0x00}, // a
    {0xE0,0x60,0x7C,0x66,0x66,0x66,0xDC,0x00}, // b
    {0x00,0x00,0x7C,0xC6,0xC0,0xC6,0x7C,0x00}, // c
    {0x1C,0x0C,0x7C,0xCC,0xCC,0xCC,0x76,0x00}, // d
    {0x00,0x00,0x7C,0xC6,0xFE,0xC0,0x7C,0x00}, // e
    {0x38,0x6C,0x60,0xF0,0x60,0x60,0xF0,0x00}, // f
    {0x00,0x00,0x76,0xCC,0xCC,0x7C,0x0C,0xF8}, // g
    {0xE0,0x60,0x6C,0x76,0x66,0x66,0xE6,0x00}, // h
    {0x18,0x00,0x38,0x18,0x18,0x18,0x3C,0x00}, // i
    {0x06,0x00,0x0E,0x06,0x06,0x66,0x66,0x3C}, // j
    {0xE0,0x60,0x66,0x6C,0x78,0x6C,0xE6,0x00}, // k
    {0x38,0x18,0x18,0x18,0x18,0x18,0x3C,0x00}, // l
    {0x00,0x00,0xCC,0xFE,0xFE,0xD6,0xD6,0x00}, // m
    {0x00,0x00,0xDC,0x66,0x66,0x66,0x66,0x00}, // n
    {0x00,0x00,0x7C,0xC6,0xC6,0xC6,0x7C,0x00}, // o
    {0x00,0x00,0xDC,0x66,0x66,0x7C,0x60,0xF0}, // p
    {0x00,0x00,0x76,0xCC,0xCC,0x7C,0x0C,0x1E}, // q
    {0x00,0x00,0xDC,0x76,0x60,0x60,0xF0,0x00}, // r
    {0x00,0x00,0x7C,0xC0,0x7C,0x06,0xFC,0x00}, // s
    {0x30,0x30,0x7C,0x30,0x30,0x34,0x18,0x00}, // t
    {0x00,0x00,0xCC,0xCC,0xCC,0xCC,0x76,0x00}, // u
    {0x00,0x00,0xC6,0xC6,0xC6,0x6C,0x38,0x00}, // v
    {0x00,0x00,0xC6,0xD6,0xFE,0xFE,0x6C,0x00}, // w
    {0x00,0x00,0xC6,0x6C,0x38,0x6C,0xC6,0x00}, // x
    {0x00,0x00,0xC6,0xC6,0xC6,0x7E,0x06,0xFC}, // y
    {0x00,0x00,0xFE,0x8C,0x18,0x32,0xFE,0x00}, // z
    {0x0E,0x18,0x18,0x70,0x18,0x18,0x0E,0x00}, // {
    {0x18,0x18,0x18,0x00,0x18,0x18,0x18,0x00}, // |
    {0x70,0x18,0x18,0x0E,0x18,0x18,0x70,0x00}, // }
    {0x76,0xDC,0x00,0x00,0x00,0x00,0x00,0x00}, // ~
    {0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00}, // DEL
};

static void fb_scroll(void) {
    u32 row_bytes = fb_pitch * 8;
    u32 *dst = (u32 *)fb_base;
    u32 *src = (u32 *)((u8 *)fb_base + row_bytes);
    u32 total = fb_pitch * (fb_h - 8) / 4;
    for (u32 i = 0; i < total; i++) dst[i] = src[i];
    u32 *last = (u32 *)((u8 *)fb_base + fb_pitch * (fb_h - 8));
    for (u32 i = 0; i < fb_pitch * 8 / 4; i++) last[i] = 0;
}

static void fb_putc(char c) {
    int cols = fb_w / 8;
    int rows = fb_h / 8;
    if (c == '\n' || c == '\r') {
        if (c == '\n') { fb_cy++; fb_cx = 0; }
        if (fb_cy >= rows) { fb_scroll(); fb_cy = rows - 1; }
        return;
    }
    if (c < 32 || c > 127) return;
    const u8 *glyph = font8x8[c - 32];
    u32 *row = (u32 *)((u8 *)fb_base + fb_cy * 8 * fb_pitch) + fb_cx * 8;
    for (int y = 0; y < 8; y++) {
        u8 bits = glyph[y];
        for (int x = 0; x < 8; x++)
            row[x] = (bits & (0x80 >> x)) ? 0x0000FF00 : 0x00000000;
        row += fb_pitch / 4;
    }
    fb_cx++;
    if (fb_cx >= cols) { fb_cx = 0; fb_cy++; }
    if (fb_cy >= rows) { fb_scroll(); fb_cy = rows - 1; }
}

/* Panel resolution — Waveshare 7" HDMI LCD is 1024x600.
 * Must match hdmi_cvt in config.txt. */
#define FB_W 1024
#define FB_H 600

/* Allocate the framebuffer via the mailbox. The Waveshare 7" HDMI LCD is
 * self-powered (separate USB), so on a cold boot HDMI/EDID can come up later
 * than the kernel — a single mailbox alloc then returns base 0 and the GPU's
 * rainbow test pattern stays on screen. Retry with a settle delay so we catch
 * the display once it's ready instead of giving up after one try. */
static int fb_alloc_once(void) {
    mbox_buf[0] = 30 * 4;
    mbox_buf[1] = 0;
    mbox_buf[2] = 0x00048003; mbox_buf[3] = 8; mbox_buf[4] = 0;
    mbox_buf[5] = FB_W; mbox_buf[6] = FB_H;
    mbox_buf[7] = 0x00048004; mbox_buf[8] = 8; mbox_buf[9] = 0;
    mbox_buf[10] = FB_W; mbox_buf[11] = FB_H;
    mbox_buf[12] = 0x00048005; mbox_buf[13] = 4; mbox_buf[14] = 0;
    mbox_buf[15] = 32;
    mbox_buf[16] = 0x00048006; mbox_buf[17] = 4; mbox_buf[18] = 0;
    mbox_buf[19] = 1;  /* pixel order: 1 = RGB */
    mbox_buf[20] = 0x00040001; mbox_buf[21] = 8; mbox_buf[22] = 0;
    mbox_buf[23] = 4096; mbox_buf[24] = 0;
    mbox_buf[25] = 0x00040008; mbox_buf[26] = 4; mbox_buf[27] = 0;
    mbox_buf[28] = 0;
    mbox_buf[29] = 0;
    dsb();
    if (!mbox_call()) return 0;
    if (!mbox_buf[23]) return 0;
    fb_base = (volatile u32 *)(u64)(mbox_buf[23] & 0x3FFFFFFF);
    fb_pitch = mbox_buf[28];
    return 1;
}

static void fb_init(void) {
    for (int try = 0; try < 20; try++) {   /* up to ~2s for HDMI to settle */
        if (fb_alloc_once()) {
            fb_w = FB_W; fb_h = FB_H;
            fb_cx = 0; fb_cy = 0;
            for (u32 i = 0; i < fb_pitch * fb_h / 4; i++) fb_base[i] = 0;
            fb_ok = 1;
            return;
        }
        delay_ms(100);
    }
}

/* ── Graphics Primitives (DRAW / persona API) ── */
static void fb_clear(u32 color) {
    if (!fb_ok) return;
    u32 stride = fb_pitch / 4;
    for (u32 y = 0; y < fb_h; y++)
        for (u32 x = 0; x < fb_w; x++)
            fb_base[y * stride + x] = color;
}

static void fb_rect(int x, int y, int w, int h, u32 color) {
    if (!fb_ok) return;
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > (int)fb_w) w = fb_w - x;
    if (y + h > (int)fb_h) h = fb_h - y;
    if (w <= 0 || h <= 0) return;
    u32 stride = fb_pitch / 4;
    for (int r = 0; r < h; r++)
        for (int c = 0; c < w; c++)
            fb_base[(y + r) * stride + x + c] = color;
}

static void fb_char(int x, int y, int scale, u32 color, char ch) {
    if (ch < 32 || ch > 127) return;
    const u8 *glyph = font8x8[ch - 32];
    for (int gy = 0; gy < 8; gy++) {
        u8 bits = glyph[gy];
        for (int gx = 0; gx < 8; gx++)
            if (bits & (0x80 >> gx))
                fb_rect(x + gx * scale, y + gy * scale, scale, scale, color);
    }
}

static void fb_text(int x, int y, int scale, u32 color, const char *s) {
    if (!fb_ok || scale < 1) return;
    while (*s) { fb_char(x, y, scale, color, *s++); x += 8 * scale; }
}

/* ── Real GPIO (BCM2711) ── */
#define GPIO_FSEL(n) (GPIO + ((n)/10)*4)
#define GPIO_SET0    (GPIO + 0x1C)
#define GPIO_CLR0    (GPIO + 0x28)
#define GPIO_LEV0    (GPIO + 0x34)

/* Pi 4 onboard ACT LED = GPIO 42 */
#define ACT_LED 42

static void act_on(void)  { wr32(GPIO_SET0 + 4, 1 << (ACT_LED - 32)); }
static void act_off(void) { wr32(GPIO_CLR0 + 4, 1 << (ACT_LED - 32)); }

static void act_blink(int n, int ms) {
    for (int i = 0; i < n; i++) {
        act_on(); delay_ms(ms);
        act_off(); delay_ms(ms);
    }
}

static void act_init(void) {
    u32 reg = rd32(GPIO_FSEL(ACT_LED));
    u32 shift = (ACT_LED % 10) * 3;
    reg &= ~(7 << shift);
    reg |= (1 << shift);
    wr32(GPIO_FSEL(ACT_LED), reg);
    act_off();
}

static void gpio_set_output(u8 pin) {
    u32 reg = rd32(GPIO_FSEL(pin));
    u32 shift = (pin % 10) * 3;
    reg &= ~(7 << shift);
    reg |= (1 << shift);
    wr32(GPIO_FSEL(pin), reg);
}

static void gpio_write(u8 pin, u8 val) {
    if (val) wr32(GPIO_SET0 + (pin/32)*4, 1 << (pin%32));
    else     wr32(GPIO_CLR0 + (pin/32)*4, 1 << (pin%32));
}

static u8 gpio_read(u8 pin) {
    return (rd32(GPIO_LEV0 + (pin/32)*4) >> (pin%32)) & 1;
}

/* ═══════════════════════════════════════════
 * Services — filled by injected resident drivers (see edge/library/<arch>/).
 * The kernel owns no device stacks; it only forwards to whatever a resident
 * registered. api_touch() is the persona-facing entry; a resident sets
 * svc.touch to serve it.
 * ═══════════════════════════════════════════ */
#include "poke_api.h"
static poke_svc_t svc;                 /* legacy kernel-mode residents (unused now) */

/* Touch service — the generic "input" side of the platform. A resident
 * process publishes raw samples (SYS_TOUCH); the kernel keeps the
 * press/hold/tap state and the sample trace that personas read. */
#define TRACE_N 128
static int tp_avail = 0, tp_down = 0, tp_new = 0, tp_x = 0, tp_y = 0;
static u32 tp_trace[TRACE_N]; static int tp_w = 0, tp_r = 0, tp_have = 0; static u32 tp_last = 0;
static void touch_publish(int x, int y, int tip) {
    if (tip == 2) { tp_avail = 1; tp_down = tp_new = 0; tp_w = tp_r = tp_have = 0; return; }
    if (tip == 3) { tp_avail = 0; tp_down = 0; return; }
    if (tip) {
        tp_x = x; tp_y = y;
        if (!tp_down) { tp_down = 1; tp_new = 1; }
    } else if (!tp_down) return;
    else tp_down = 0;
    tp_trace[tp_w] = (u32)(tp_x & 0xffff) | ((u32)(tp_y & 0x7fff) << 16) | (tip ? 0x80000000u : 0);
    tp_w = (tp_w + 1) % TRACE_N; if (tp_w == tp_r) tp_r = (tp_r + 1) % TRACE_N;
}
static int api_touch(int *x, int *y) {
    if (svc.touch) return svc.touch(x, y);
    if (x) *x = tp_x;
    if (y) *y = tp_y;
    if (!tp_avail || !tp_down) return 0;
    if (tp_new) { tp_new = 0; return 2; }
    return 1;
}
static int api_touch_trace(unsigned int *out, int max) {
    if (svc.touch_trace) return svc.touch_trace(out, max);
    int n = 0;
    if (tp_r == tp_w) return 0;
    if (tp_have && (tp_last >> 31) && n < max) out[n++] = tp_last;
    while (tp_r != tp_w && n < max) { out[n++] = tp_trace[tp_r]; tp_r = (tp_r + 1) % TRACE_N; }
    tp_last = out[n-1]; tp_have = 1;
    return n;
}

/* ── Wall Clock (set by hub via TIME command) ── */
static u64 epoch_ms_base = 0;   /* epoch ms at the moment TIME was set */
static u64 epoch_set_cnt = 0;   /* timer count at that moment */

static u64 now_ms(void) { return timer_cnt() / (timer_frq() / 1000); }

static u64 wall_sec(void) {
    if (!epoch_ms_base) return 0;
    return (epoch_ms_base + (timer_cnt() - epoch_set_cnt) * 1000 / timer_frq()) / 1000;
}

/* ═══════════════════════════════════════════
 * Persona API — function table handed to
 * hub-generated resident code (x0 = api ptr)
 * Field order is ABI: never reorder, only append.
 * ═══════════════════════════════════════════ */

static void api_gpio_out(u8 pin, u8 val) { gpio_set_output(pin); gpio_write(pin, val); }

/* Runtime parameters set by the hub (PPAR) — lets one cached persona
 * binary serve many requests ("3-minute timer" = timer + param[0]=180) */
static u64 persona_params[8];
static u64 api_param(int idx) { return persona_params[idx & 7]; }

/* ── Autonomous events: persona → hub (EVNT on port 5556) ──
 * The edge watches its own conditions and speaks only when needed —
 * no hub polling. Rate-limited per event code. */
static void send_udp(const u8 *dst_mac, u32 dst_ip, u16 dport, u16 sport,
                     const u8 *payload, int plen);   /* fwd decl */
static u8  peer_mac[6];
static u32 peer_ip = 0;
static u16 peer_port = 0;
static int eth_up = 0;

#define POKE_PORT 5555
#define EVNT_PORT 5556

static void api_emit(unsigned int code, unsigned long value) {
    static u64 emit_last[4];
    if (!eth_up || !peer_ip) return;
    u64 t = now_ms();
    if (t - emit_last[code & 3] < 10000) return;   /* ≥10s per code */
    emit_last[code & 3] = t;

    u8 p[16];
    p[0]='E'; p[1]='V'; p[2]='N'; p[3]='T';
    for (int i = 0; i < 4; i++) p[4 + i] = (code >> (i * 8)) & 0xFF;
    for (int i = 0; i < 8; i++) p[8 + i] = (value >> (i * 8)) & 0xFF;
    send_udp(peer_mac, peer_ip, EVNT_PORT, POKE_PORT, p, 16);
    uprint("[EVNT] code="); udec(code); uprint(" val="); udec((u32)value); uputc('\n');
}

/* last few api->log lines (residents/units), for remote diagnosis via INFO */
static char last_logs[4][96]; static int last_log_i = 0;
static void api_log(const char *m) {
    uprint(m);
    int n = 0; char *d = last_logs[last_log_i % 4];
    while (m[n] && m[n] != '\n' && n < 95) { d[n] = m[n]; n++; }
    if (n) { d[n] = 0; last_log_i++; }
}
static unsigned int api_screen(void) { return fb_ok ? ((fb_w << 16) | fb_h) : 0; }

static const api_t persona_api = {
    fb_clear, fb_rect, fb_text, now_ms, wall_sec,
    api_gpio_out, gpio_read, get_soc_temp, api_param, api_touch, api_emit,
    &svc, api_log, api_screen, api_touch_trace,
};

/* ── Persona Slot (resident binary, called every tick) ── */
#define PERSONA_SZ 8192
static u8 persona_buf[PERSONA_SZ] __attribute__((aligned(4096)));
static u64 persona_tick = 0;
static u64 persona_last_ms = 0;

typedef u64 (*persona_fn_t)(const api_t *api, u64 tick);
static persona_fn_t persona_fn = 0;

static int unit_persona_start(const u8 *code, int clen);   /* EL0 path (defined with the unit code) */
static void unit_persona_stop(void);
static void unit_persona_run(void);
static void persona_stop(void) {
    unit_persona_stop();
    persona_active = 0;
    persona_fn = 0;
    mset(persona_buf, 0, PERSONA_SZ);  /* volatile: discard on stop */
}

static int persona_load(const u8 *code, int clen) {
    if (clen <= 0 || clen > PERSONA_SZ) return -1;
    if (mmu_on) return unit_persona_start(code, clen);      /* personas are EL0 processes now */

    int has_ret = 0;
    for (int i = 0; i <= clen - 4; i += 4) {
        u32 insn = code[i] | (code[i+1]<<8) | (code[i+2]<<16) | ((u32)code[i+3]<<24);
        if (insn == 0xD65F03C0) { has_ret = 1; break; }
    }
    if (!has_ret) return -2;

    persona_active = 0;
    mcpy(persona_buf, code, clen);
    for (u64 a = (u64)persona_buf; a < (u64)persona_buf + (u64)clen; a += 64) {
        __asm__ volatile("dc civac, %0" :: "r"(a));
        __asm__ volatile("ic ivau, %0" :: "r"(a));
    }
    __asm__ volatile("dsb sy"); __asm__ volatile("isb");

    persona_fn = (persona_fn_t)persona_buf;
    persona_tick = 0;
    persona_last_ms = now_ms();
    persona_active = 1;
    return 0;
}

static void persona_run(void) {
    if (persona_active && !persona_fn) { unit_persona_run(); return; }   /* EL0 persona */
    if (!persona_active || !persona_fn) return;
    u64 t = now_ms();
    if (t - persona_last_ms < PERSONA_TICK_MS) return;
    persona_last_ms = t;
    persona_fn(&persona_api, persona_tick++);
}

/* ── Network Config ── */
static const u8 our_mac[6] = {0x02, 0x50, 0x4F, 0x4B, 0x45, 0x04};
#define OUR_IP      0x0A000002U   /* 10.0.0.2 */

/* ── Packet Structs ── */
typedef struct __attribute__((packed)) { u8 dst[6]; u8 src[6]; u16 type; } eth_t;
typedef struct __attribute__((packed)) {
    u16 htype; u16 ptype; u8 hlen; u8 plen; u16 oper;
    u8 sha[6]; u8 spa[4]; u8 tha[6]; u8 tpa[4];
} arp_t;
typedef struct __attribute__((packed)) {
    u8 vihl; u8 tos; u16 len; u16 id; u16 frag;
    u8 ttl; u8 proto; u16 cksum; u32 src; u32 dst;
} ip_t;
typedef struct __attribute__((packed)) { u16 sport; u16 dport; u16 len; u16 cksum; } udp_t;

/* ═══════════════════════════════════════════
 * GENET v5 Ethernet Controller
 * ═══════════════════════════════════════════ */

/* Register helpers */
static inline u32 grd(u32 off) { return rd32(GENET + off); }
static inline void gwr(u32 off, u32 v) { wr32(GENET + off, v); }

/* Block offsets */
#define G_SYS   0x0000
#define G_EXT   0x0080
#define G_RBUF  0x0300
#define G_TBUF  0x0600
#define G_UMAC  0x0800

/* DMA: 256 BDs × 12 bytes each, then 17 rings × 64 bytes, then global */
#define G_RDMA  0x2000
#define G_TDMA  0x4000
#define NUM_BD  256
#define BD_SZ   12
#define RING_SZ 0x40

/* BD fields */
#define BD(base,i,f)          ((base) + (i)*BD_SZ + (f))
#define BD_STAT 0
#define BD_ALO  4
#define BD_AHI  8

/* Ring register: base + NUM_BD*12 + ring*64 + reg */
#define RING(base,r,reg)      ((base) + NUM_BD*BD_SZ + (r)*RING_SZ + (reg))
/* Global DMA: base + NUM_BD*12 + 17*64 + reg */
#define DMA_G(base,reg)       ((base) + NUM_BD*BD_SZ + 17*RING_SZ + (reg))

/* Ring regs — NOTE: RX and TX have different layouts (per Linux bcmgenet)
 * RX: WRITE_PTR 0x00, PROD 0x08 (hw), CONS 0x0C (sw)
 * TX: READ_PTR  0x00, CONS 0x08 (hw), PROD 0x0C (sw)  */
#define R_WR    0x00
#define R_PI    0x08
#define R_CI    0x0C
#define R_BSZ   0x10
#define R_SA    0x14
#define R_EA    0x1C
#define R_DT    0x24
#define R_XON   0x28   /* RX: XON/XOFF thresholds | TX: flow period */
#define R_RD    0x2C
#define RT_RD   0x00
#define RT_CI   0x08
#define RT_PI   0x0C
#define RT_WR   0x2C

/* Global DMA regs */
#define D_RCFG  0x00
#define D_CTRL  0x04
#define D_STAT  0x08
#define D_SCB   0x0C
#define D_ARB   0x2C
#define D_PRIO0 0x30
#define D_PRIO1 0x34
#define D_PRIO2 0x38

/* UMAC registers */
#define U_CMD   (G_UMAC + 0x008)
#define U_MAC0  (G_UMAC + 0x00C)
#define U_MAC1  (G_UMAC + 0x010)
#define U_MFL   (G_UMAC + 0x014)
#define U_MDIO  (G_UMAC + 0x614)

/* DMA bus address: GENET sits on the BCM2711 scb bus whose dma-ranges
 * are identity-mapped — use raw physical addresses (the 0xC0000000
 * alias is only for legacy VPU-bus peripherals) */
#define DMA(phys) ((u64)(phys))

/* Buffer pool in RAM.
 * RX ring 16 owns all 256 RDMA descriptors (Circle/Linux layout).
 * TX queues 0-3 own TDMA BDs 0-127 (configured, unused);
 * TX ring 16 owns TDMA BDs 128-255. */
#define BUF_SZ    2048
#define NRX       256
#define NTXQ      128
#define TXQ_START 128
#define RX_BASE 0x02000000ULL              /* 256×2048 = 512KB */
#define TX_BASE 0x02100000ULL              /* 128×2048 = 256KB */

static u32 rx_ci = 0;
static u32 tx_pi = 0;
static int crc_fwd = 0;

/* ── MDIO ── */
#define PHY_ADDR 1

static u16 mdio_rd(u8 reg) {
    gwr(U_MDIO, (1<<29) | (2<<26) | ((u32)PHY_ADDR<<21) | ((u32)reg<<16));
    delay_us(50);
    int tries = 1000;
    while ((grd(U_MDIO) & (1<<29)) && --tries) delay_us(10);
    return grd(U_MDIO) & 0xFFFF;
}

static void mdio_wr(u8 reg, u16 val) {
    gwr(U_MDIO, (1<<29) | (1<<26) | ((u32)PHY_ADDR<<21) | ((u32)reg<<16) | val);
    delay_us(50);
    int tries = 1000;
    while ((grd(U_MDIO) & (1<<29)) && --tries) delay_us(10);
}

/* ── PHY (BCM54213PE) — non-blocking link state machine ── */
static int phy_ok = 0;

static int phy_setup(void) {
    /* Power up PHY via EXT_GPHY_CTRL */
    u32 gc = grd(G_EXT + 0x00);
    gc &= ~((1<<0)|(1<<1)|(1<<4));  /* clear IDDQ, PWR_DOWN, CK25_DIS */
    gc |= (1<<5);                    /* assert GPHY_RESET */
    gwr(G_EXT + 0x00, gc);
    delay_ms(2);
    gc &= ~(1<<5);                   /* de-assert GPHY_RESET */
    gwr(G_EXT + 0x00, gc);
    delay_ms(50);

    u16 id1 = mdio_rd(2), id2 = mdio_rd(3);
    uprint("[PHY] id="); uhex32((id1 << 16) | id2); uputc('\n');
    if (id1 == 0xFFFF || id1 == 0) { uprint("[PHY] not found\n"); return -1; }

    mdio_wr(0, 1 << 15);  /* reset */
    delay_ms(100);
    for (int i = 0; i < 100; i++) { if (!(mdio_rd(0) & (1<<15))) break; delay_ms(10); }
    return 0;
}

/* Link strategies, cycled every 8s while link is down */
static void phy_config(int mode) {
    switch (mode & 3) {
    case 0:  /* AN 10/100/1000 */
        mdio_wr(4, 0x01E1); mdio_wr(9, 0x0300);
        mdio_wr(0, (1<<12) | (1<<9));
        uprint("[PHY] try AN 10/100/1000\n");
        break;
    case 1:  /* AN 10/100 only */
        mdio_wr(4, 0x01E1); mdio_wr(9, 0);
        mdio_wr(0, (1<<12) | (1<<9));
        uprint("[PHY] try AN 10/100\n");
        break;
    case 2:  /* force 100 full-duplex */
        mdio_wr(9, 0);
        mdio_wr(0, 0x2100);
        uprint("[PHY] try force 100FD\n");
        break;
    case 3:  /* force 10 full-duplex */
        mdio_wr(9, 0);
        mdio_wr(0, 0x0100);
        uprint("[PHY] try force 10FD\n");
        break;
    }
}

/* Returns speed if link is up, 0 if down */
static int phy_link_speed(void) {
    mdio_rd(1);              /* BMSR link bit is latched-low: discard 1st read */
    u16 bmsr = mdio_rd(1);
    if (!(bmsr & (1<<2))) return 0;
    u16 bmcr = mdio_rd(0);
    if (!(bmcr & (1<<12))) return (bmcr & (1<<13)) ? 100 : 10;  /* forced mode */
    u16 gbsr = mdio_rd(10), lpa = mdio_rd(5);
    return (gbsr & 0x0C00) ? 1000 : (lpa & 0x0180) ? 100 : 10;
}

/* ── GENET Init — mirrors Circle bcm54213.cpp / Linux bcmgenet ── */
static void umac_soft_reset(void) {
    gwr(G_SYS + 0x08, 0);            /* SYS_RBUF_FLUSH_CTRL = 0 */
    delay_us(10);
    gwr(U_CMD, 0);
    gwr(U_CMD, (1 << 13) | (1 << 15));  /* SW_RESET + LCL_LOOP_EN (stable rxclk) */
    delay_us(2);
    gwr(U_CMD, 0);
}

static void genet_init(void) {
    u32 rev = grd(G_SYS + 0x00);
    uprint("[GENET] rev="); uhex32(rev); uputc('\n');

    /* reset_umac + umac_reset2 */
    umac_soft_reset();
    gwr(G_SYS + 0x08, 2); delay_us(10);   /* RBUF_FLUSH_CTRL bit1 pulse */
    gwr(G_SYS + 0x08, 0); delay_us(10);

    /* init_umac */
    umac_soft_reset();
    gwr(G_UMAC + 0x580, 7);  /* MIB_CTRL: reset RX/TX/RUNT counters */
    gwr(G_UMAC + 0x580, 0);
    gwr(U_MFL, 1536);
    gwr(G_RBUF + 0x00, grd(G_RBUF + 0x00) | (1 << 1));  /* RBUF_ALIGN_2B */
    gwr(G_RBUF + 0xB4, 1);                              /* RBUF_TBUF_SIZE_CTRL */
    crc_fwd = (grd(U_CMD) >> 6) & 1;                    /* CMD_CRC_FWD */

    /* MAC address */
    gwr(U_MAC0, (our_mac[0]<<24) | (our_mac[1]<<16) | (our_mac[2]<<8) | our_mac[3]);
    gwr(U_MAC1, (our_mac[4]<<8) | our_mac[5]);

    /* Port mode: external RGMII gigabit PHY (default is internal EPHY —
     * without this the RGMII pads are not connected to the UMAC at all) */
    gwr(G_SYS + 0x04, 3);  /* SYS_PORT_CTRL = PORT_MODE_EXT_GPHY */
    gwr(G_EXT + 0x0C, grd(G_EXT + 0x0C) | (1 << 6) | (1 << 16));  /* RGMII_MODE_EN | ID_MODE_DIS */

    /* dma_disable + TX flush */
    gwr(DMA_G(G_TDMA, D_CTRL), grd(DMA_G(G_TDMA, D_CTRL)) & ~((1 << 17) | 1));
    gwr(DMA_G(G_RDMA, D_CTRL), grd(DMA_G(G_RDMA, D_CTRL)) & ~((1 << 17) | 1));
    gwr(G_UMAC + 0x334, 1); delay_us(10); gwr(G_UMAC + 0x334, 0);  /* UMAC_TX_FLUSH */

    /* ── RDMA: ring 16 owns all 256 descriptors ── */
    gwr(DMA_G(G_RDMA, D_SCB), 8);  /* SCB burst */
    for (int i = 0; i < NRX; i++) {
        u64 da = DMA(RX_BASE + i * BUF_SZ);
        gwr(BD(G_RDMA, i, BD_ALO), (u32)da);
        gwr(BD(G_RDMA, i, BD_AHI), (u32)(da >> 32));
    }
    gwr(RING(G_RDMA, 16, R_PI), 0);
    gwr(RING(G_RDMA, 16, R_CI), 0);
    gwr(RING(G_RDMA, 16, R_BSZ), (NRX << 16) | BUF_SZ);
    gwr(RING(G_RDMA, 16, R_XON), (5 << 16) | (NRX >> 4));  /* XOFF=5, XON=16 */
    gwr(RING(G_RDMA, 16, R_SA), 0);
    gwr(RING(G_RDMA, 16, R_RD), 0);
    gwr(RING(G_RDMA, 16, R_WR), 0);
    gwr(RING(G_RDMA, 16, R_EA), NRX * 3 - 1);
    gwr(DMA_G(G_RDMA, D_RCFG), (1 << 16));
    gwr(DMA_G(G_RDMA, D_CTRL), (1 << 17));  /* ring16 buf en, DMA_EN later */

    /* ── TDMA: queues 0-3 (BDs 0-127, unused) + ring 16 (BDs 128-255) ── */
    gwr(DMA_G(G_TDMA, D_SCB), 8);
    gwr(DMA_G(G_TDMA, D_ARB), 2);  /* strict priority arbiter */
    for (int q = 0; q < 4; q++) {
        gwr(RING(G_TDMA, q, RT_PI), 0);
        gwr(RING(G_TDMA, q, RT_CI), 0);
        gwr(RING(G_TDMA, q, R_DT), 10);            /* MBUF_DONE_THRESH */
        gwr(RING(G_TDMA, q, R_XON), 1536 << 16);   /* flow period */
        gwr(RING(G_TDMA, q, R_BSZ), (32 << 16) | BUF_SZ);
        gwr(RING(G_TDMA, q, R_SA), q * 32 * 3);
        gwr(RING(G_TDMA, q, RT_RD), q * 32 * 3);
        gwr(RING(G_TDMA, q, RT_WR), q * 32 * 3);
        gwr(RING(G_TDMA, q, R_EA), (q + 1) * 32 * 3 - 1);
    }
    gwr(RING(G_TDMA, 16, RT_PI), 0);
    gwr(RING(G_TDMA, 16, RT_CI), 0);
    gwr(RING(G_TDMA, 16, R_DT), 10);
    gwr(RING(G_TDMA, 16, R_XON), 0);
    gwr(RING(G_TDMA, 16, R_BSZ), (NTXQ << 16) | BUF_SZ);
    gwr(RING(G_TDMA, 16, R_SA), TXQ_START * 3);
    gwr(RING(G_TDMA, 16, RT_RD), TXQ_START * 3);
    gwr(RING(G_TDMA, 16, RT_WR), TXQ_START * 3);
    gwr(RING(G_TDMA, 16, R_EA), NUM_BD * 3 - 1);
    /* queue priorities: q0-3 → 0-3, ring16 → 4 */
    gwr(DMA_G(G_TDMA, D_PRIO0), (0 << 0) | (1 << 5) | (2 << 10) | (3 << 15));
    gwr(DMA_G(G_TDMA, D_PRIO1), 0);
    gwr(DMA_G(G_TDMA, D_PRIO2), 4 << 20);
    gwr(DMA_G(G_TDMA, D_RCFG), 0x1000F);  /* rings 0-3 + 16 */
    gwr(DMA_G(G_TDMA, D_CTRL), (0xF << 1) | (1 << 17));

    /* enable DMA */
    gwr(DMA_G(G_RDMA, D_CTRL), grd(DMA_G(G_RDMA, D_CTRL)) | (1 << 17) | 1);
    gwr(DMA_G(G_TDMA, D_CTRL), grd(DMA_G(G_TDMA, D_CTRL)) | (1 << 17) | 1);

    rx_ci = 0;
    tx_pi = 0;
}

static void genet_stop(void) {                     /* halt RX/TX DMA: no more writes into RAM */
    gwr(DMA_G(G_TDMA, D_CTRL), grd(DMA_G(G_TDMA, D_CTRL)) & ~((1 << 17) | 1));
    gwr(DMA_G(G_RDMA, D_CTRL), grd(DMA_G(G_RDMA, D_CTRL)) & ~((1 << 17) | 1));
    delay_ms(5);
}

static void genet_enable(int speed) {
    /* mii_setup: RGMII link + clock select */
    u32 oob = grd(G_EXT + 0x0C);
    oob &= ~(1 << 5);        /* clear OOB_DISABLE */
    oob |= (1 << 4);         /* RGMII_LINK */
    gwr(G_EXT + 0x0C, oob);

    u32 cmd = grd(U_CMD);
    cmd &= ~(3 << 2);
    if (speed == 1000) cmd |= (2 << 2);
    else if (speed == 100) cmd |= (1 << 2);
    cmd |= (1 << 8) | (1u << 28);  /* ignore pause frames both directions */
    cmd |= (1 << 4);               /* promiscuous */
    gwr(U_CMD, cmd);

    /* netif_start */
    cmd |= 3;  /* TX_EN | RX_EN */
    gwr(U_CMD, cmd);

    eth_up = 1;
    uprint("[GENET] enabled\n");
}

/* ── Ethernet TX / RX ── */
/* Returns: >0 frame length (data at *out), -1 bad frame (still consume),
 * 0 nothing pending */
static int eth_rx(u8 **out) {
    u32 pi = grd(RING(G_RDMA, 16, R_PI)) & 0xFFFF;
    if ((rx_ci & 0xFFFF) == pi) return 0;

    u32 idx = rx_ci % NRX;
    u32 st = grd(BD(G_RDMA, idx, BD_STAT));
    int len = (st >> 16) & 0xFFF;
    u32 flags = st & 0xFFFF;

    /* must be a whole frame (SOP+EOP), no error bits */
    if (!(flags & 0x2000) || !(flags & 0x4000)) return -1;
    if (flags & 0x001F) return -1;  /* OV|CRC|RXER|NO|LG */

    *out = (u8 *)(RX_BASE + idx * BUF_SZ) + 2;  /* skip RBUF_ALIGN_2B pad */
    len -= 2;
    if (crc_fwd) len -= 4;
    return (len >= 14) ? len : -1;
}

static void eth_rx_done(void) {
    u32 idx = rx_ci % NRX;
    u64 da = DMA(RX_BASE + idx * BUF_SZ);
    gwr(BD(G_RDMA, idx, BD_ALO), (u32)da);
    gwr(BD(G_RDMA, idx, BD_AHI), (u32)(da >> 32));
    rx_ci = (rx_ci + 1) & 0xFFFF;
    gwr(RING(G_RDMA, 16, R_CI), rx_ci);
}

static u8 tx_buf[2048];

static int eth_tx(const u8 *frame, int len) {
    if (len < 14 || len > 1518) return -1;
    u32 ci = grd(RING(G_TDMA, 16, RT_CI)) & 0xFFFF;
    if (((tx_pi - ci) & 0xFFFF) >= NTXQ - 1) return -2;

    /* Pad to minimum ethernet frame size (60 bytes, FCS added by HW) */
    int txlen = len < 60 ? 60 : len;
    u32 slot = tx_pi % NTXQ;
    u32 idx = TXQ_START + slot;            /* ring16 BDs are 128-255 */
    u8 *dst = (u8 *)(TX_BASE + slot * BUF_SZ);
    mcpy(dst, frame, len);
    if (txlen > len) mset(dst + len, 0, txlen - len);
    dsb();

    u64 da = DMA(TX_BASE + slot * BUF_SZ);
    gwr(BD(G_TDMA, idx, BD_ALO), (u32)da);
    gwr(BD(G_TDMA, idx, BD_AHI), (u32)(da >> 32));
    /* len | QTAG(0x3F<<7) | SOP | EOP | APPEND_CRC */
    gwr(BD(G_TDMA, idx, BD_STAT), (txlen << 16) | (0x3F << 7) | 0x6040);
    dsb();

    tx_pi = (tx_pi + 1) & 0xFFFF;
    gwr(RING(G_TDMA, 16, RT_PI), tx_pi);
    return 0;
}

/* ═══════════════════════════════════════════
 * Network Stack (ARP / IPv4 / UDP / ICMP)
 * ═══════════════════════════════════════════ */

/* Send ARP reply */
static void arp_reply(const u8 *req) {
    eth_t *re = (eth_t *)req;
    arp_t *ra = (arp_t *)(req + 14);

    u32 tpa;
    mcpy(&tpa, ra->tpa, 4);
    if (ntohl(tpa) != OUR_IP) return;

    eth_t *e = (eth_t *)tx_buf;
    arp_t *a = (arp_t *)(tx_buf + 14);
    mcpy(e->dst, re->src, 6); mcpy(e->src, our_mac, 6); e->type = htons(0x0806);
    a->htype = htons(1); a->ptype = htons(0x0800); a->hlen = 6; a->plen = 4;
    a->oper = htons(2);
    mcpy(a->sha, our_mac, 6);
    u32 sip = htonl(OUR_IP); mcpy(a->spa, &sip, 4);
    mcpy(a->tha, ra->sha, 6); mcpy(a->tpa, ra->spa, 4);
    eth_tx(tx_buf, 42);
}

/* Send ARP request (probe the hub — proves TX path independently) */
static void arp_request(u32 target_ip) {
    static const u8 bcast[6] = {0xFF,0xFF,0xFF,0xFF,0xFF,0xFF};
    eth_t *e = (eth_t *)tx_buf;
    arp_t *a = (arp_t *)(tx_buf + 14);
    mcpy(e->dst, bcast, 6); mcpy(e->src, our_mac, 6); e->type = htons(0x0806);
    a->htype = htons(1); a->ptype = htons(0x0800); a->hlen = 6; a->plen = 4;
    a->oper = htons(1);
    mcpy(a->sha, our_mac, 6);
    u32 sip = htonl(OUR_IP); mcpy(a->spa, &sip, 4);
    mset(a->tha, 0, 6);
    u32 tip = htonl(target_ip); mcpy(a->tpa, &tip, 4);
    eth_tx(tx_buf, 42);
}

/* Send ICMP echo reply */
static void icmp_reply(const u8 *frame, int len) {
    if (len < 14 + 20 + 8) return;
    eth_t *re = (eth_t *)frame;
    ip_t *ri = (ip_t *)(frame + 14);
    u8 *ricmp = (u8 *)(frame + 14 + 20);

    if (ricmp[0] != 8) return;  /* not echo request */

    int iplen = ntohs(ri->len);
    int total = 14 + iplen;
    if (total > 1500) return;

    mcpy(tx_buf, frame, total);
    eth_t *e = (eth_t *)tx_buf;
    ip_t *ip = (ip_t *)(tx_buf + 14);
    u8 *icmp = tx_buf + 14 + 20;

    mcpy(e->dst, re->src, 6); mcpy(e->src, our_mac, 6);
    ip->dst = ri->src; ip->src = htonl(OUR_IP);
    ip->cksum = 0; ip->cksum = ip_cksum(ip, 20);

    icmp[0] = 0;  /* echo reply */
    icmp[2] = 0; icmp[3] = 0;
    u16 icksum = ip_cksum(icmp, iplen - 20);
    icmp[2] = icksum & 0xFF; icmp[3] = (icksum >> 8) & 0xFF;

    eth_tx(tx_buf, total);
}

/* Send UDP packet */
static u16 ipid = 0;

static void send_udp(const u8 *dst_mac, u32 dst_ip, u16 dport, u16 sport,
                     const u8 *payload, int plen) {
    int total = 14 + 20 + 8 + plen;
    if (total > 1500) return;
    mset(tx_buf, 0, total);

    eth_t *e = (eth_t *)tx_buf;
    ip_t *ip = (ip_t *)(tx_buf + 14);
    udp_t *u = (udp_t *)(tx_buf + 34);
    u8 *d = tx_buf + 42;

    mcpy(e->dst, dst_mac, 6); mcpy(e->src, our_mac, 6); e->type = htons(0x0800);

    ip->vihl = 0x45; ip->len = htons(20 + 8 + plen);
    ip->id = htons(ipid++); ip->ttl = 64; ip->proto = 17;
    ip->src = htonl(OUR_IP); ip->dst = htonl(dst_ip);
    ip->cksum = 0; ip->cksum = ip_cksum(ip, 20);

    u->sport = htons(sport); u->dport = htons(dport);
    u->len = htons(8 + plen); u->cksum = 0;

    mcpy(d, payload, plen);
    eth_tx(tx_buf, total);
}

/* ═══════════════════════════════════════════
 * POKE Protocol over UDP
 * ═══════════════════════════════════════════ */

#ifdef NO_ETH
/* QEMU twin: the protocol rides the mini UART (QEMU serial #1), which the
 * launcher exposes as a TCP socket. Same frames as UDP, just a byte stream. */
#define AUX_BASE (PERI + 0x215000)
static void twin_uart_init(void) {
    wr32(AUX_BASE + 0x04, rd32(AUX_BASE + 0x04) | 1);   /* AUX_ENABLES: mini UART */
    wr32(AUX_BASE + 0x60, 0);                             /* CNTL: off while configuring */
    wr32(AUX_BASE + 0x44, 0);                             /* IER: no interrupts */
    wr32(AUX_BASE + 0x4C, 3);                             /* LCR: 8 bit */
    wr32(AUX_BASE + 0x50, 0);
    wr32(AUX_BASE + 0x48, 0xC6);                          /* IIR: clear FIFOs */
    wr32(AUX_BASE + 0x68, 270);                           /* BAUD (ignored by QEMU) */
    wr32(AUX_BASE + 0x60, 3);                             /* CNTL: RX + TX */
}
static void twin_putc(u8 c) { u32 t = 100000; while (!(rd32(AUX_BASE + 0x54) & (1 << 5)) && --t) { } wr32(AUX_BASE + 0x40, c); }
static int twin_getc(void) { return (rd32(AUX_BASE + 0x54) & 1) ? (int)(rd32(AUX_BASE + 0x40) & 0xFF) : -1; }
static void poke_resp(const u8 *data, int len) {
    u8 h[8] = { 'R', 'E', 'S', 'P', (u8)len, (u8)(len >> 8), (u8)(len >> 16), (u8)(len >> 24) };
    for (int i = 0; i < 8; i++) twin_putc(h[i]);
    for (int i = 0; i < len; i++) twin_putc(data[i]);
}
#else
static void poke_resp(const u8 *data, int len) {
    u8 rbuf[1400];
    rbuf[0]='R'; rbuf[1]='E'; rbuf[2]='S'; rbuf[3]='P';
    rbuf[4]=len&0xFF; rbuf[5]=(len>>8)&0xFF; rbuf[6]=(len>>16)&0xFF; rbuf[7]=(len>>24)&0xFF;
    if (len > 0 && len <= 1392) mcpy(rbuf + 8, data, len);
    send_udp(peer_mac, peer_ip, peer_port, POKE_PORT, rbuf, 8 + len);
}
#endif

static void poke_resp_str(const char *s) { poke_resp((const u8 *)s, slen(s)); }

/* ── Code Execution ── */
#define CODE_SZ 4096
static u8 code_buf[CODE_SZ] __attribute__((aligned(4096)));
static char res_buf[256];
static int res_len = 0;

/* Run whatever is already staged in code_buf[0..clen). Shared by the
 * single-frame EXEC path and the chunked EXLD/EXRN path. */
static void run_staged(int clen) {
    if (clen <= 0 || clen > CODE_SZ) { poke_resp_str("error: size"); return; }

    int has_ret = 0;
    for (int i = 0; i <= clen - 4; i += 4) {
        u32 insn = code_buf[i] | (code_buf[i+1]<<8) | (code_buf[i+2]<<16) | (code_buf[i+3]<<24);
        if (insn == 0xD65F03C0) { has_ret = 1; break; }
    }

    mset(res_buf, 0, 256);
    res_len = 0;
    u64 ret = 0;

    if (has_ret) {
        /* Flush the whole staged range, not just the first line — a chunked
         * upload can be several KB. */
        for (u32 o = 0; o < (u32)clen; o += 64) {
            __asm__ volatile("dc civac, %0" :: "r"(code_buf + o));
            __asm__ volatile("ic ivau, %0" :: "r"(code_buf + o));
        }
        __asm__ volatile("dsb sy"); __asm__ volatile("isb");
        u64 (*fn)(char *, int *) = (u64 (*)(char *, int *))code_buf;
        ret = fn(res_buf, &res_len);
    }

    /* res_buf is 256 bytes — rsp must be at least as large, or a long probe
     * reply overruns the kernel stack (seen as a PC-alignment fault with an
     * ASCII ELR). */
    char rsp[256]; int rl = 0;
    if (!has_ret) { rl = scpy(rsp, "no RET"); }
    else if (res_len > 0) { mcpy(rsp, res_buf, res_len); rl = res_len; }
    else { rl = scpy(rsp, "x0="); rl += idec((u32)ret, rsp + rl); }
    poke_resp((const u8 *)rsp, rl);
}

static void handle_exec(const u8 *code, int clen) {
    if (clen <= 0 || clen > CODE_SZ) { poke_resp_str("error: size"); return; }
    mcpy(code_buf, code, clen);
    run_staged(clen);
}

/* ── Chunked upload ──
 * A binary larger than one MTU frame is uploaded in pieces: EXLD chunks land
 * in stage_buf, then EXRN (run as probe) or PRST (load as persona) consumes
 * it. No reliance on IP fragment reassembly, which the bare-metal stack does
 * not do. The run/load commands carry the total length and a Fletcher-32
 * checksum so a lost chunk is refused instead of executed. Single-frame
 * EXEC / PRUN stay as they are for small binaries. */
#define STAGE_SZ 262144   /* kernels reload through here too */
static u8  stage_buf[STAGE_SZ] __attribute__((aligned(64)));
static u32 stage_hi = 0;            /* highest staged end offset */

static u32 ld32(const u8 *p) { return p[0] | (p[1]<<8) | (p[2]<<16) | ((u32)p[3]<<24); }
static u32 fletcher32(const u8 *d, u32 n) {
    u32 s1 = 0, s2 = 0;
    for (u32 i = 0; i < n; i++) { s1 = (s1 + d[i]) % 65535; s2 = (s2 + s1) % 65535; }
    return (s2 << 16) | s1;
}

static void handle_exld(const u8 *p, int len) {
    if (len < 4) { poke_resp_str("error: exld short"); return; }
    u32 off = ld32(p);
    int nb = len - 4;
    if (off > STAGE_SZ || nb < 0 || off + (u32)nb > STAGE_SZ) { poke_resp_str("error: exld range"); return; }
    if (off == 0) stage_hi = 0;                 /* a new upload starts at 0 */
    mcpy(stage_buf + off, p + 4, nb);
    if (off + (u32)nb > stage_hi) stage_hi = off + (u32)nb;
    char r[24]; int n = scpy(r, "ok "); n += idec(off + (u32)nb, r + n);
    poke_resp((const u8 *)r, n);
}

/* Validate a staged upload: payload = total_len(4 LE) [+ fletcher32(4 LE)].
 * Returns the length, or 0 after sending the error reply. */
static u32 stage_check(const u8 *p, int len, const char *who) {
    if (len < 4) { poke_resp_str("{\"error\":\"short\"}"); return 0; }
    u32 clen = ld32(p);
    if (clen == 0 || clen > STAGE_SZ || clen > stage_hi) { poke_resp_str("{\"error\":\"stage length\"}"); return 0; }
    if (len >= 8 && ld32(p + 4) != fletcher32(stage_buf, clen)) {
        uprint("[POKE] "); uprint(who); uprint(" checksum mismatch\n");
        poke_resp_str("{\"error\":\"checksum\"}"); return 0;
    }
    return clen;
}

/* KRLD: replace the running kernel with the staged image — no SD card swap.
 * Reply first (the frame must leave through TX DMA), quiesce DMA masters so
 * nothing writes into the new image's memory while it boots, then let a
 * trampoline outside the image copy it to 0x80000 and restart at _start. */
static void genet_stop(void);
static void resident_stop(void);
extern char reload_tramp[], reload_tramp_end[];
static void handle_krld(const u8 *p, int len) {
    u32 clen = stage_check(p, len, "KRLD"); if (!clen) return;
    if (clen < 1024 || (stage_buf[0] | stage_buf[1] | stage_buf[2] | stage_buf[3]) == 0) { poke_resp_str("{\"error\":\"not a kernel\"}"); return; }
    char r[64]; int n = scpy(r, "{\"kernel\":\"reloading\",\"size\":"); n += idec(clen, r+n); r[n++] = '}';
    poke_resp((const u8 *)r, n);
    uprint("[POKE] KRLD "); udec(clen); uprint(" bytes — restarting\n");
    delay_ms(30);
    resident_stop();
    genet_stop();
    u64 tramp = 0x00700000ULL; u32 tl = (u32)(reload_tramp_end - reload_tramp);
    mcpy((void *)tramp, reload_tramp, tl);
    __asm__ volatile("dsb sy; ic iallu; dsb sy; isb" ::: "memory");
    ((void (*)(u64, u64))tramp)((u64)stage_buf, clen);
}

static void handle_exrn(const u8 *p, int len) {
    u32 clen = stage_check(p, len, "EXRN"); if (!clen) return;
    if (clen > CODE_SZ) { poke_resp_str("error: size"); return; }
    mcpy(code_buf, stage_buf, clen);
    run_staged((int)clen);
}

/* ── Console home screen (boot banner) ── */
static void print_banner(void) {
    uprint("\n");
    uprint("  ____   ___  _  _______ \n");
    uprint(" |  _ \\ / _ \\| |/ / ____|\n");
    uprint(" | |_) | | | | ' /|  _|  \n");
    uprint(" |  __/| |_| | . \\| |___ \n");
    uprint(" |_|    \\___/|_|\\_\\_____|\n");
    uprint("\n");
    uprint("  POKE OS Pi 4 v0.2 (bare-metal ethernet)\n");
    uprint("  arch: aarch64 (Cortex-A72)\n");
    uprint("  transport: UDP over GENET v5\n\n");
}

static void console_home(void) {
    if (fb_ok) {
        u32 stride = fb_pitch / 4;
        for (u32 i = 0; i < stride * fb_h; i++) fb_base[i] = 0;
        fb_cx = 0; fb_cy = 0;
    }
    print_banner();
    if (eth_up) {
        uprint("[NET] IP: "); uip(OUR_IP); uprint(":"); udec(POKE_PORT); uputc('\n');
    }
    uprint("[PERSONA] none — binary discarded (volatile)\n\n");
    uprint("poke-pi4> ");
}

/* ── LE readers for DRAW op stream ── */
static int gs16(const u8 *p) { return (short)(p[0] | (p[1] << 8)); }
static u32 gu32(const u8 *p) { return p[0] | (p[1]<<8) | (p[2]<<16) | ((u32)p[3]<<24); }

/* DRAW op stream: [op ...]*
 *   op 1 CLEAR: color u32                                  (5B)
 *   op 2 RECT:  x i16, y i16, w i16, h i16, color u32      (13B)
 *   op 3 TEXT:  x i16, y i16, scale u8, color u32, len u8, chars (11+len B)
 */
static void handle_draw(const u8 *p, int rem) {
    if (!fb_ok) { poke_resp_str("{\"error\":\"no display\"}"); return; }
    int ops = 0;
    while (rem > 0) {
        u8 op = p[0];
        if (op == 1 && rem >= 5) {
            fb_clear(gu32(p + 1));
            p += 5; rem -= 5;
        } else if (op == 2 && rem >= 13) {
            fb_rect(gs16(p+1), gs16(p+3), gs16(p+5), gs16(p+7), gu32(p+9));
            p += 13; rem -= 13;
        } else if (op == 3 && rem >= 11) {
            int tl = p[10];
            if (rem < 11 + tl) break;
            char tb[129];
            int cl = tl > 128 ? 128 : tl;
            mcpy(tb, p + 11, cl); tb[cl] = 0;
            fb_text(gs16(p+1), gs16(p+3), p[5], gu32(p+6), tb);
            p += 11 + tl; rem -= 11 + tl;
        } else break;
        ops++;
    }
    char r[32]; int n = scpy(r, "{\"ops\":"); n += idec(ops, r+n); r[n++] = '}';
    poke_resp((const u8 *)r, n);
}

static u64 mmu_ram_total, pool_pages, unit_resumes, unit_preempts;
static u64 pages_used(void);
static void handle_urun(const u8 *p, int len);
static void handle_rsld(const u8 *p, int len);
static int apply_mappings(const u8 *p, int len);
static void handle_mlod(const u8 *p, int len);
static void handle_matt(const u8 *p, int len);
static void handle_mstp(const u8 *p, int len);
static void handle_msto(void);
static void handle_sreq(const u8 *p, int len);
static const char *resident_name(void);   /* NULL when no resident process is running */
/* resident slot state (defined with the resident mechanism below; INFO reports it) */
static void resident_stop(void);

/* ── POKE Command Router ── */
static void handle_poke(const u8 *payload, int len) {
    if (len < 4) { poke_resp_str("error: short"); return; }

    if (mcmp(payload, "PING", 4) == 0) {
        poke_resp_str("PONG");
        uprint("[POKE] PING\n");
    }
    else if (mcmp(payload, "INFO", 4) == 0) {
        u32 temp = get_soc_temp();
        char r[1400]; int n = 0;
        n += scpy(r+n, "{\"status\":\"alive\",\"arch\":\"aarch64\",\"chip\":\"bcm2711\"");
        n += scpy(r+n, ",\"kernel\":\"poke-os\",\"build\":\"" BUILD_ID "\",\"transport\":\"udp\"");
        { u64 el; __asm__ volatile("mrs %0, CurrentEL" : "=r"(el)); n += scpy(r+n, ",\"el\":"); n += idec((u32)((el >> 2) & 3), r+n); }
        n += scpy(r+n, ",\"mmu\":"); n += scpy(r+n, mmu_on ? "true" : "false");
        n += scpy(r+n, ",\"ram_mb\":"); n += idec((u32)(mmu_ram_total >> 20), r+n);
        n += scpy(r+n, ",\"pool_pages\":"); n += idec((u32)pool_pages, r+n);
        n += scpy(r+n, ",\"pool_used\":"); n += idec((u32)pages_used(), r+n);
        n += scpy(r+n, ",\"unit_resumes\":"); n += idec((u32)unit_resumes, r+n);
        n += scpy(r+n, ",\"unit_preempts\":"); n += idec((u32)unit_preempts, r+n);
        n += scpy(r+n, ",\"last_log\":[");
        for (int k = 0; k < 4; k++) { const char *l = last_logs[(last_log_i + k) % 4]; if (!l[0]) continue;
            r[n++] = '"'; for (int i = 0; l[i] && n < 1300; i++) { if (l[i] == '"' || l[i] == '\\') r[n++] = '\\'; r[n++] = l[i]; } r[n++] = '"'; r[n++] = ','; }
        if (r[n-1] == ',') n--; r[n++] = ']';
        n += scpy(r+n, ",\"ip\":\"10.0.0.2\",\"port\":5555");
        n += scpy(r+n, ",\"commands\":[\"PING\",\"INFO\",\"EXEC\",\"EXLD\",\"EXRN\",\"PRST\",\"RSLD\",\"RSTP\",\"KRLD\",\"URUN\",\"SREQ\",\"MLOD\",\"MATT\",\"MSTP\",\"MSTO\",\"GPIO\",\"GPOS\",\"TEMP\",\"DRAW\",\"PRUN\",\"PSTP\",\"PPAR\",\"TIME\"]");
        if (fb_ok) {
            n += scpy(r+n, ",\"display\":\""); n += idec(fb_w, r+n); r[n++] = 'x'; n += idec(fb_h, r+n); r[n++] = '"';
            n += scpy(r+n, ",\"fb_base\":"); n += idec((u32)(u64)fb_base, r+n);
            n += scpy(r+n, ",\"fb_pitch\":"); n += idec(fb_pitch, r+n);
        } else {
            n += scpy(r+n, ",\"display\":null");
        }
        n += scpy(r+n, ",\"touch\":"); n += scpy(r+n, (svc.touch || tp_avail) ? "true" : "false");
        n += scpy(r+n, ",\"resident\":"); if (resident_name()) { r[n++]='"'; n += scpy(r+n, resident_name()); r[n++]='"'; } else n += scpy(r+n, "null");
        n += scpy(r+n, ",\"persona\":"); n += scpy(r+n, persona_active ? "true" : "false");
        n += scpy(r+n, ",\"bare_metal\":true");
        n += scpy(r+n, ",\"temp_mc\":"); n += idec(temp, r+n);
        r[n++] = '}';
        poke_resp((const u8 *)r, n);
        uprint("[POKE] INFO\n");
    }
    else if (mcmp(payload, "EXEC", 4) == 0) {
        uprint("[POKE] EXEC "); udec(len-4); uprint(" bytes\n");
        handle_exec(payload + 4, len - 4);
    }
    else if (mcmp(payload, "EXLD", 4) == 0) {
        handle_exld(payload + 4, len - 4);
    }
    else if (mcmp(payload, "EXRN", 4) == 0) {
        uprint("[POKE] EXRN\n");
        handle_exrn(payload + 4, len - 4);
    }
    else if (mcmp(payload, "GPIO", 4) == 0) {
        char r[256]; int n = 0;
        n += scpy(r+n, "{\"pins\":{");
        for (int p = 2; p <= 27; p++) {
            if (p == 14 || p == 15) continue;  /* skip UART */
            if (p > 2) r[n++] = ',';
            r[n++] = '"'; n += idec(p, r+n); r[n++] = '"'; r[n++] = ':';
            r[n++] = '0' + gpio_read(p);
        }
        n += scpy(r+n, "}}");
        poke_resp((const u8 *)r, n);
        uprint("[POKE] GPIO\n");
    }
    else if (mcmp(payload, "GPOS", 4) == 0) {
        if (len < 6) { poke_resp_str("{\"error\":\"need pin+val\"}"); return; }
        u8 pin = payload[4], val = payload[5];
        if (pin < 2 || pin > 27 || pin == 14 || pin == 15) {
            poke_resp_str("{\"error\":\"invalid pin\"}"); return;
        }
        gpio_set_output(pin);
        gpio_write(pin, val);
        char r[64]; int n = 0;
        n += scpy(r+n, "{\"pin\":"); n += idec(pin, r+n);
        n += scpy(r+n, ",\"value\":"); r[n++] = '0' + (val ? 1 : 0);
        r[n++] = '}';
        poke_resp((const u8 *)r, n);
        uprint("[POKE] GPIO "); udec(pin); uprint("="); udec(val); uputc('\n');
    }
    else if (mcmp(payload, "TEMP", 4) == 0) {
        u32 mc = get_soc_temp();
        u32 deg = mc / 1000, frac = (mc % 1000) / 100;
        char r[64]; int n = 0;
        n += scpy(r+n, "{\"celsius\":"); n += idec(deg, r+n);
        r[n++] = '.'; r[n++] = '0' + frac;
        n += scpy(r+n, ",\"raw_mc\":"); n += idec(mc, r+n);
        r[n++] = '}';
        poke_resp((const u8 *)r, n);
        uprint("[POKE] TEMP "); udec(deg); uputc('.'); udec(frac); uprint("C\n");
    }
    else if (mcmp(payload, "DRAW", 4) == 0) {
        handle_draw(payload + 4, len - 4);
        uprint("[POKE] DRAW\n");
    }
    else if (mcmp(payload, "PRUN", 4) == 0) {
        int rc = persona_load(payload + 4, len - 4);
        if (rc == 0) {
            char r[64]; int n = scpy(r, "{\"persona\":\"running\",\"size\":");
            n += idec(len - 4, r+n); r[n++] = '}';
            poke_resp((const u8 *)r, n);
            uprint("[POKE] PRUN "); udec(len-4); uprint(" bytes\n");
        } else {
            poke_resp_str(rc == -2 ? "{\"error\":\"no RET\"}" : "{\"error\":\"size\"}");
        }
    }
    else if (mcmp(payload, "PRST", 4) == 0) {      /* persona from staged chunks */
        u32 clen = stage_check(payload + 4, len - 4, "PRST");
        if (clen) {
            int rc = persona_load(stage_buf, (int)clen);
            if (rc == 0) {
                char r[64]; int n = scpy(r, "{\"persona\":\"running\",\"size\":");
                n += idec(clen, r+n); r[n++] = '}';
                poke_resp((const u8 *)r, n);
                uprint("[POKE] PRST "); udec(clen); uprint(" bytes\n");
            } else {
                poke_resp_str(rc == -2 ? "{\"error\":\"no RET\"}" : "{\"error\":\"size\"}");
            }
        }
    }
    else if (mcmp(payload, "RSLD", 4) == 0) {      /* resident driver → EL0 process with capabilities */
        handle_rsld(payload + 4, len - 4);
    }
    else if (mcmp(payload, "SREQ", 4) == 0) {      /* request to the resident driver */
        handle_sreq(payload + 4, len - 4);
    }
    else if (mcmp(payload, "MLOD", 4) == 0) {      /* motion skill → EL0 unit */
        handle_mlod(payload + 4, len - 4);
    }
    else if (mcmp(payload, "MATT", 4) == 0) {      /* start an attempt with params */
        handle_matt(payload + 4, len - 4);
    }
    else if (mcmp(payload, "MSTP", 4) == 0) {      /* one control tick */
        handle_mstp(payload + 4, len - 4);
    }
    else if (mcmp(payload, "MSTO", 4) == 0) {      /* stop and discard the skill */
        handle_msto();
    }
    else if (mcmp(payload, "URUN", 4) == 0) {
        handle_urun(payload + 4, len - 4);
    }
    else if (mcmp(payload, "KRLD", 4) == 0) {
        handle_krld(payload + 4, len - 4);
    }
    else if (mcmp(payload, "RSTP", 4) == 0) {
        resident_stop();
        poke_resp_str("{\"resident\":\"stopped\"}");
    }
    else if (mcmp(payload, "PSTP", 4) == 0) {
        persona_stop();
        poke_resp_str("{\"persona\":\"stopped\"}");
        console_home();
    }
    else if (mcmp(payload, "PPAR", 4) == 0) {
        if (len < 5) { poke_resp_str("{\"error\":\"need count\"}"); return; }
        int np = payload[4]; if (np > 8) np = 8;
        if (len < 5 + np * 8) { poke_resp_str("{\"error\":\"short\"}"); return; }
        for (int i = 0; i < np; i++) {
            u64 v = 0;
            for (int b = 7; b >= 0; b--) v = (v << 8) | payload[5 + i*8 + b];
            persona_params[i] = v;
        }
        char r[32]; int rn = scpy(r, "{\"params\":"); rn += idec(np, r+rn); r[rn++] = '}';
        poke_resp((const u8 *)r, rn);
        uprint("[POKE] PPAR "); udec(np); uputc('\n');
    }
    else if (mcmp(payload, "TIME", 4) == 0) {
        if (len < 12) { poke_resp_str("{\"error\":\"need epoch_ms u64\"}"); return; }
        epoch_ms_base = 0;
        for (int i = 7; i >= 0; i--) epoch_ms_base = (epoch_ms_base << 8) | payload[4 + i];
        epoch_set_cnt = timer_cnt();
        poke_resp_str("{\"clock\":\"set\"}");
        uprint("[POKE] TIME\n");
    }
    else {
        poke_resp_str("error: unknown");
    }
}

/* ═══════════════════════════════════════════
 * Packet Processor
 * ═══════════════════════════════════════════ */

static void process_frame(u8 *frame, int len) {
    if (len < 14) return;
    eth_t *e = (eth_t *)frame;
    u16 etype = ntohs(e->type);

    /* ARP */
    if (etype == 0x0806 && len >= 42) {
        arp_t *a = (arp_t *)(frame + 14);
        if (ntohs(a->oper) == 1) arp_reply(frame);
        /* oper==2 (reply): silent */
        return;
    }

    /* IPv4 */
    if (etype != 0x0800 || len < 34) return;
    ip_t *ip = (ip_t *)(frame + 14);
    if ((ip->vihl & 0xF0) != 0x40) return;
    if (ntohl(ip->dst) != OUR_IP && ntohl(ip->dst) != 0xFFFFFFFF) return;

    /* ICMP */
    if (ip->proto == 1) { icmp_reply(frame, len); return; }

    /* UDP */
    if (ip->proto != 17 || len < 42) return;
    udp_t *u = (udp_t *)(frame + 34);
    int udp_len = ntohs(u->len) - 8;
    u8 *data = frame + 42;

    /* Store peer info for response */
    mcpy(peer_mac, e->src, 6);
    peer_ip = ntohl(ip->src);
    peer_port = ntohs(u->sport);

    /* POKE frame: "POKE" + len(4 LE) + payload */
    if (ntohs(u->dport) == POKE_PORT && udp_len >= 8) {
        if (data[0]=='P' && data[1]=='O' && data[2]=='K' && data[3]=='E') {
            u32 plen = data[4] | (data[5]<<8) | (data[6]<<16) | (data[7]<<24);
            if (plen <= (u32)(udp_len - 8)) {
                handle_poke(data + 8, plen);
            }
        }
    }
}

/* ═══════════════════════════════════════════
 * Fault Handler — called from exception vectors
 * ═══════════════════════════════════════════ */

static void uhex64(u64 v) {
    const char h[] = "0123456789abcdef";
    uprint("0x");
    for (int i = 60; i >= 0; i -= 4) uputc(h[(v >> i) & 0xF]);
}

void fault_handler(u64 kind, u64 esr, u64 elr, u64 far) {
    persona_active = 0;  /* reclaim the framebuffer console */
    uprint("\n*** FAULT kind="); udec((u32)kind);
    uprint("\n  ESR="); uhex64(esr);
    uprint("\n  ELR="); uhex64(elr);
    uprint("\n  FAR="); uhex64(far);
    uprint("\n  EC=");  uhex32((u32)(esr >> 26));
    uputc('\n');
    /* park with SOS blink */
    while (1) {
        act_blink(3, 100); act_blink(3, 300); act_blink(3, 100);
        delay_ms(700);
    }
}

/* ═══════════════════════════════════════════
 * Kernel Main
 * ═══════════════════════════════════════════ */


/* Resident drivers are EL0 processes now (SLOT_RESIDENT, see units below).
 * resident_stop() asks the driver to run RES_STOP and exit, then reclaims it. */
static void resident_stop(void);

/* ═══════════════════════════════════════════
 * MMU: identity map (VA == PA) with memory attributes — the kernel keeps
 * physical addresses; the point is caching and Device typing. Everything a
 * DMA engine or the GPU touches stays non-cacheable, so no cache maintenance
 * is needed anywhere in the kernel:
 *   [0,16MB)          kernel image/.bss (GENET rings, code/persona/stage/
 *                     resident buffers), stack, page tables → NC
 *   [0x02000000,+4MB) GENET RX/TX packet buffers, xHCI DMA region → NC
 *   [ARM end, 1GB)    GPU / framebuffer → NC
 *   [0xFC000000,4GB)  peripherals → Device
 *   0x600000000 (1GB) PCIe outbound window (VL805 MMIO) → Device
 *   everything else   RAM → write-back cacheable (process pages come from here)
 * Tables at 0x00800000: L1 (64 × 1GB, T0SZ=28) + 2MB tables for GB0/GB3.
 * ═══════════════════════════════════════════ */
#define MMU_L1   0x00800000ULL
#define MMU_GB0  0x00801000ULL
#define MMU_GB3  0x00802000ULL
#define MMU_L0   0x00803000ULL   /* level 0: 512 × 512GB — [0] = kernel identity (MMU_L1), [32] = a unit's VA */

#define ATTR_WB  0
#define ATTR_NC  1
#define ATTR_DEV 2
#define PT_BLOCK(pa, attr) ((pa) | 0x1ULL | ((u64)(attr) << 2) | (3ULL << 8) | (1ULL << 10))
#define PT_TABLE(pa)       ((pa) | 0x3ULL)
static u64 mmu_arm_end = 0x3E600000ULL;
static int mmu_on = 0;

static void mmu_cache_invalidate_all(void) {          /* dc isw over every data/unified level */
    u64 clidr; __asm__ volatile("mrs %0, clidr_el1" : "=r"(clidr));
    int loc = (clidr >> 24) & 7;
    for (int lvl = 0; lvl < loc; lvl++) {
        if (((clidr >> (lvl*3)) & 7) < 2) continue;
        __asm__ volatile("msr csselr_el1, %0" :: "r"((u64)lvl << 1)); __asm__ volatile("isb");
        u64 cc; __asm__ volatile("mrs %0, ccsidr_el1" : "=r"(cc));
        int line = (cc & 7) + 4, ways = ((cc >> 3) & 0x3ff) + 1, sets = ((cc >> 13) & 0x7fff) + 1;
        int wshift = ways > 1 ? __builtin_clz((u32)ways - 1) : 32;
        for (int w = 0; w < ways; w++) for (int st = 0; st < sets; st++) {
            u64 v = ((u64)lvl << 1) | ((u64)st << line) | (ways > 1 ? ((u64)w << wshift) : 0);
            __asm__ volatile("dc isw, %0" :: "r"(v));
        }
    }
    __asm__ volatile("dsb sy");
}

static void mmu_init(void) {
    /* GPU boundary (ARM memory tag) and total RAM (board revision) from the mailbox */
    mbox_buf[0] = 8*4; mbox_buf[1] = 0; mbox_buf[2] = 0x00010005; mbox_buf[3] = 8; mbox_buf[4] = 0; mbox_buf[5] = 0; mbox_buf[6] = 0; mbox_buf[7] = 0;
    dsb();
    if (mbox_call() && mbox_buf[6]) mmu_arm_end = (u64)mbox_buf[5] + mbox_buf[6];
    mbox_buf[0] = 8*4; mbox_buf[1] = 0; mbox_buf[2] = 0x00010002; mbox_buf[3] = 4; mbox_buf[4] = 0; mbox_buf[5] = 0; mbox_buf[6] = 0; mbox_buf[7] = 0;
    dsb();
    if (mbox_call() && (mbox_buf[5] & (1u << 23))) { static const u32 mb[] = {256, 512, 1024, 2048, 4096, 8192, 0, 0}; mmu_ram_total = (u64)mb[(mbox_buf[5] >> 20) & 7] << 20; }
    if (!mmu_ram_total) mmu_ram_total = mmu_arm_end;
    volatile u64 *l0 = (volatile u64 *)MMU_L0;
    for (int i = 0; i < 512; i++) l0[i] = 0;
    l0[0] = PT_TABLE(MMU_L1);

    volatile u64 *l1 = (volatile u64 *)MMU_L1, *gb0 = (volatile u64 *)MMU_GB0, *gb3 = (volatile u64 *)MMU_GB3;
    for (int i = 0; i < 512; i++) { l1[i] = 0; gb0[i] = 0; gb3[i] = 0; }
    for (int i = 0; i < 16; i++) l1[i] = PT_BLOCK((u64)i << 30, ATTR_WB);
    l1[0] = PT_TABLE(MMU_GB0);
    l1[3] = PT_TABLE(MMU_GB3);
    l1[24] = PT_BLOCK(0x600000000ULL, ATTR_DEV);                /* PCIe outbound window */
    for (int i = 0; i < 512; i++) {
        u64 pa = (u64)i << 21; int a = ATTR_WB;
        if (pa < 0x01000000ULL) a = ATTR_NC;
        else if (pa >= 0x02000000ULL && pa < 0x02400000ULL) a = ATTR_NC;   /* GENET RX/TX buffers + xHCI DMA */
        else if (pa >= mmu_arm_end) a = ATTR_NC;
        gb0[i] = PT_BLOCK(pa, a);
        u64 pa3 = 0xC0000000ULL + ((u64)i << 21);
        gb3[i] = PT_BLOCK(pa3, pa3 >= 0xFC000000ULL ? ATTR_DEV : ATTR_WB);
    }
    dsb();
    if (fb_ok) fb_text(40, 470, 2, 0x00AAAAAA, "mmu: tables");
    mmu_cache_invalidate_all(); __asm__ volatile("ic iallu; dsb sy; isb");
    if (fb_ok) fb_text(40, 490, 2, 0x00AAAAAA, "mmu: caches invalidated");
    /* Cortex-A72: no data caching without CPUECTLR_EL1.SMPEN */
    { u64 e; __asm__ volatile("mrs %0, s3_1_c15_c2_1" : "=r"(e)); e |= (1ULL << 6); __asm__ volatile("msr s3_1_c15_c2_1, %0" :: "r"(e)); __asm__ volatile("isb"); }
    if (fb_ok) fb_text(40, 510, 2, 0x00AAAAAA, "mmu: smpen");
    u64 mair = 0xFFULL | (0x44ULL << 8) | (0x04ULL << 16);
    u64 el; __asm__ volatile("mrs %0, CurrentEL" : "=r"(el)); el = (el >> 2) & 3;
    if (el == 2) {
        u64 tcr = (1ULL << 31) | (1ULL << 23) | (2ULL << 16) | (3ULL << 12) | 28;    /* EL2: RES1, PS 40-bit, SH0 inner, 4KB, T0SZ=28 */
        __asm__ volatile("msr mair_el2, %0" :: "r"(mair));
        __asm__ volatile("msr tcr_el2, %0" :: "r"(tcr));
        __asm__ volatile("msr ttbr0_el2, %0" :: "r"((u64)MMU_L1));
        __asm__ volatile("tlbi alle2; dsb sy; isb");
        if (fb_ok) fb_text(40, 530, 2, 0x00AAAAAA, "mmu: regs set (EL2), enabling...");
        u64 sc; __asm__ volatile("mrs %0, sctlr_el2" : "=r"(sc)); sc |= (1ULL << 0) | (1ULL << 2) | (1ULL << 12);
        __asm__ volatile("msr sctlr_el2, %0" :: "r"(sc)); __asm__ volatile("isb");
    } else {
        /* EL1: IPS 40-bit, EPD1, T1SZ=16, SH0 inner, walks WB-cacheable (unit tables live in
         * cacheable RAM), 4KB, T0SZ=16 → 48-bit VA from a level-0 table */
        u64 tcr = (2ULL << 32) | (1ULL << 23) | (16ULL << 16) | (3ULL << 12) | (1ULL << 10) | (1ULL << 8) | 16;
        __asm__ volatile("msr mair_el1, %0" :: "r"(mair));
        __asm__ volatile("msr tcr_el1, %0" :: "r"(tcr));
        __asm__ volatile("msr ttbr0_el1, %0" :: "r"((u64)MMU_L0));
        __asm__ volatile("dsb sy; tlbi vmalle1is; dsb sy; isb");
        if (fb_ok) fb_text(40, 530, 2, 0x00AAAAAA, "mmu: regs set (EL1), enabling...");
        u64 sc; __asm__ volatile("mrs %0, sctlr_el1" : "=r"(sc)); sc |= (1ULL << 0) | (1ULL << 2) | (1ULL << 12);
        __asm__ volatile("msr sctlr_el1, %0" :: "r"(sc)); __asm__ volatile("isb");
    }
    mmu_on = 1;
    if (el == 1) { __asm__ volatile("msr cntkctl_el1, %0" :: "r"(3ULL)); }   /* EL0PCTEN|EL0VCTEN: units may read the timer */
    if (fb_ok) fb_text(40, 550, 2, 0x0000FF66, el == 2 ? "mmu: ON (EL2)" : "mmu: ON (EL1)");
}

/* ═══════════════════════════════════════════
 * Units: injected binaries running at EL0 in their own address space.
 *   VA layout (per unit, level-0 slot 32 = 0x1000_0000_0000):
 *     UNIT_BASE           image (text/rodata/data/bss), page-granular
 *     UNIT_BASE+0x100000  stack (one page, grows down from +0x101000)
 *   The kernel's identity map (L0[0]) is shared into every unit table with
 *   EL1-only permissions, so the kernel keeps working with any TTBR0.
 * Pages come from a bitmap allocator over RAM the kernel never touches.
 * Phase 2: one unit at a time, run synchronously (proc_enter → SVC exit).
 * ═══════════════════════════════════════════ */
#define UNIT_BASE   0x100000000000ULL
#define UNIT_STACK  (UNIT_BASE + 0x100000ULL)
#define PAGE        4096ULL
#define PT_PAGE_USER(pa) ((pa) | 0x3ULL | (1ULL << 6) | (3ULL << 8) | (1ULL << 10) | (1ULL << 53))  /* AttrIdx0 WB, AP=EL0 RW, SH inner, AF, PXN */
#define UNIT_MAX_PAGES 96
#define SYS_LOG   0
#define SYS_EXIT  1
#define SYS_YIELD 2
#define SYS_SLEEP 3
#define SYS_MBOX  4
#define SYS_TOUCH 5
#define SYS_WAIT  6           /* park until the kernel resumes this unit explicitly (motion step) */
#define SYS_API   10          /* 10..23 = api_t function slots in order (svc slot skipped) */
#define UNIT_SLEEPING 0x51ee
#define SLICE_MS 4
#define UNIT_BUDGET_MS 3000
static void timer_arm(u32 ms);
static void timer_off(void);
#define UNIT_DEAD     0xdead
#define FRAME_WORDS   102     /* 816-byte EL0 exception frame: GP + q0-q31 + fpcr/fpsr */

/* ── physical page allocator ── */
static u64 pool_base = 0, pool_pages = 0;
static u32 pool_map[(3ULL << 30) / PAGE / 32];      /* enough for 3GB of pool */
static u64 pool_next = 0;
static void pool_init(void) {
    if (mmu_ram_total > 0x40000000ULL) { pool_base = 0x40000000ULL; u64 end = mmu_ram_total < 0xFC000000ULL ? mmu_ram_total : 0xFC000000ULL; pool_pages = (end - pool_base) / PAGE; }
    else { pool_base = 0x03000000ULL; pool_pages = (mmu_arm_end - pool_base) / PAGE; }
    if (pool_pages > sizeof(pool_map) * 8) pool_pages = sizeof(pool_map) * 8;
    mset(pool_map, 0, sizeof pool_map);
}
static u64 page_alloc(void) {
    for (u64 n = 0; n < pool_pages; n++) {
        u64 i = (pool_next + n) % pool_pages;
        if (!(pool_map[i / 32] & (1u << (i % 32)))) {
            pool_map[i / 32] |= 1u << (i % 32); pool_next = i + 1;
            u64 pa = pool_base + i * PAGE;
            mset((void *)pa, 0, PAGE);
            return pa;
        }
    }
    return 0;
}
static void page_free(u64 pa) {
    if (pa < pool_base) return;
    u64 i = (pa - pool_base) / PAGE; if (i >= pool_pages) return;
    pool_map[i / 32] &= ~(1u << (i % 32));
}
static u64 pages_used(void) { u64 c = 0; for (u64 i = 0; i < pool_pages; i++) if (pool_map[i / 32] & (1u << (i % 32))) c++; return c; }

/* ── unit (process) ── */
struct unit_s {
    u64 l0, l1, l2, l3;                  /* table pages */
    u64 pages[UNIT_MAX_PAGES]; int npages;
    u64 entry, sp;
    int active; u64 exit_code;
    int sleeping; u64 wake_at; u64 ctx[FRAME_WORDS];   /* parked in a sleep syscall (or preempted) */
    u64 cpu_ms, budget_ms;                             /* CPU used since the last voluntary sleep / limit */
    u64 svc_pa;                                        /* residents: service page (SREQ/SRSP channel) */
    int preempted;                                     /* parked by the timer, not by sleep() */
    int stop_req;                                      /* next sleep() returns 1: run STOP and exit */
    char name[32];
    int is_persona;
    char log[512]; int loglen;
};
extern u64 proc_resume(u64 *ctx, u64 ttbr0);
extern char ucrt_start[], ucrt_stubs[], ucrt_end[];
#define UNIT_SLOTS 4
#define SLOT_URUN 0                      /* transient `poke unit` jobs */
#define SLOT_PERSONA 1
#define SLOT_RESIDENT 2
#define SLOT_SKILL 3                     /* generated motion skill, stepped by MSTP */
typedef struct unit_s unit_t;
static unit_t units[UNIT_SLOTS];
static unit_t *cur = &units[0];          /* the unit the EL0 paths operate on */
static const char *resident_name(void) { return units[SLOT_RESIDENT].active ? units[SLOT_RESIDENT].name : (const char *)0; }
extern u64 proc_enter(u64 entry, u64 sp_el0, u64 ttbr0);
extern void proc_exit(u64 code) __attribute__((noreturn));

static void unit_log(const char *m) { uprint("[UNIT] "); uprint(m); uputc('\n'); api_log(m); int n = 0; while (m[n] && cur->loglen < (int)sizeof(cur->log) - 2) cur->log[cur->loglen++] = m[n++]; cur->log[cur->loglen++] = '\n'; }

static void unit_destroy(void) {
    for (int i = 0; i < cur->npages; i++) page_free(cur->pages[i]);
    if (cur->l3) page_free(cur->l3);
    if (cur->l2) page_free(cur->l2);
    if (cur->l1) page_free(cur->l1);
    if (cur->l0) page_free(cur->l0);
    mset(cur, 0, sizeof *cur);
}

/* Build the address space and load the image. 0 ok, <0 error. */
extern char rcrt_start[];
static void unit_tables_clean(void);
static int unit_create(const u8 *img, u32 len, int persona) {   /* persona: 0 plain, 1 persona crt, 2 resident crt */
    mset(cur, 0, sizeof *cur);
    cur->budget_ms = (persona == 2) ? 60000 : UNIT_BUDGET_MS;   /* a driver's init busy-waits on hardware */
    u32 npg = (len + PAGE - 1) / PAGE;
    u32 first = persona ? 1 : 0;                              /* crt page 0, image from page 1 */
    if (first + npg + 4 > UNIT_MAX_PAGES) return -1;
    cur->l0 = page_alloc(); cur->l1 = page_alloc(); cur->l2 = page_alloc(); cur->l3 = page_alloc();
    if (!cur->l0 || !cur->l1 || !cur->l2 || !cur->l3) { unit_destroy(); return -2; }
    volatile u64 *l0 = (volatile u64 *)cur->l0, *l1 = (volatile u64 *)cur->l1, *l2 = (volatile u64 *)cur->l2, *l3 = (volatile u64 *)cur->l3;
    l0[0] = ((volatile u64 *)MMU_L0)[0];                    /* kernel identity, EL1-only */
    l0[(UNIT_BASE >> 39) & 0x1ff] = PT_TABLE(cur->l1);
    l1[(UNIT_BASE >> 30) & 0x1ff] = PT_TABLE(cur->l2);
    l2[(UNIT_BASE >> 21) & 0x1ff] = PT_TABLE(cur->l3);       /* image pages: l3[first..] */
    if (persona) {                                            /* crt page: loop + api stubs + table */
        u64 pa = page_alloc(); if (!pa) { unit_destroy(); return -3; }
        cur->pages[cur->npages++] = pa;
        mcpy((void *)pa, ucrt_start, (u32)(ucrt_end - ucrt_start));   /* both crts + stubs live in one blob */
        volatile u64 *d = (volatile u64 *)(pa + 0x800);
        d[0] = UNIT_BASE + PAGE;                              /* persona_main / resident_main */
        d[1] = UNIT_BASE + 0x820;                             /* api table VA */
        d[2] = (persona == 2) ? 2 : PERSONA_TICK_MS;          /* residents tick every 2 ms */
        u64 stub0 = UNIT_BASE + (u64)(ucrt_stubs - ucrt_start);
        volatile u64 *api = (volatile u64 *)(pa + 0x820);
        int k = 0;
        for (int i = 0; i < 15; i++) api[i] = (i == 11) ? 0 : stub0 + (u64)(k++) * 16;   /* slot 11 = svc (data), personas never use it */
        for (u64 a = pa; a < pa + PAGE; a += 64) { __asm__ volatile("dc cvau, %0" :: "r"(a)); }
        l3[0] = PT_PAGE_USER(pa);
    }
    for (u32 i = 0; i < npg; i++) {
        u64 pa = page_alloc(); if (!pa) { unit_destroy(); return -3; }
        cur->pages[cur->npages++] = pa;
        u32 n = len - i * PAGE; if (n > PAGE) n = PAGE;
        mcpy((void *)pa, img + i * PAGE, n);
        for (u64 a = pa; a < pa + PAGE; a += 64) { __asm__ volatile("dc cvau, %0" :: "r"(a)); }
        l3[first + i] = PT_PAGE_USER(pa);
    }
    for (int i = 0; i < 4; i++) {                             /* 16KB stack ending at UNIT_STACK+PAGE */
        u64 sp = page_alloc(); if (!sp) { unit_destroy(); return -3; }
        cur->pages[cur->npages++] = sp;
        l3[((UNIT_STACK >> 12) & 0x1ff) - 3 + i] = PT_PAGE_USER(sp);
    }
    __asm__ volatile("dsb ish; ic iallu; dsb ish; isb");
    unit_tables_clean();
    cur->entry = UNIT_BASE + ((persona == 2) ? (u64)(rcrt_start - ucrt_start) : 0); cur->sp = UNIT_STACK + PAGE;
    cur->active = 1; cur->is_persona = persona;
    return 0;
}

/* ── capability mapping: give the unit EL0 access to a physical range
 * (VA == PA), 4KB-granular. The kernel's identity tables are shared into
 * every unit; the branches that need EL0 entries are cloned on demand
 * (L1 for the low 512GB, the L2 for that GB, an L3 for that 2MB). ── */
#define PT_PAGE_K(pa, attr)   ((pa) | 0x3ULL | ((u64)(attr) << 2) | (3ULL << 8) | (1ULL << 10) | (1ULL << 53))
#define PT_PAGE_U(pa, attr)   ((pa) | 0x3ULL | ((u64)(attr) << 2) | (1ULL << 6) | (3ULL << 8) | (1ULL << 10) | (1ULL << 53) | (1ULL << 54))
/* The table walker does not see the data cache the way the CPU does: push
 * every table page the unit owns to DRAM before the tables go live. */
static void unit_tables_clean(void) {
    u64 pg[4] = { cur->l0, cur->l1, cur->l2, cur->l3 };
    for (int i = 0; i < 4; i++) if (pg[i]) for (u64 a = pg[i]; a < pg[i] + PAGE; a += 64) __asm__ volatile("dc civac, %0" :: "r"(a));
    for (int i = 0; i < cur->npages; i++) for (u64 a = cur->pages[i]; a < cur->pages[i] + PAGE; a += 64) __asm__ volatile("dc civac, %0" :: "r"(a));
    __asm__ volatile("dsb sy; tlbi vmalle1is; dsb sy; isb");
}
static u64 unit_table_page(void) { u64 pa = page_alloc(); if (pa && cur->npages < UNIT_MAX_PAGES) cur->pages[cur->npages++] = pa; return pa; }
static int unit_map(u64 pa, u64 len, int attr) {
    if (pa >= (1ULL << 39) || cur->npages + 3 > UNIT_MAX_PAGES) return -1;
    volatile u64 *l0 = (volatile u64 *)cur->l0;
    if ((l0[0] & ~0xfffULL) == MMU_L1) {                          /* clone the kernel L1 */
        u64 n = unit_table_page(); if (!n) return -2;
        mcpy((void *)n, (void *)MMU_L1, PAGE); l0[0] = PT_TABLE(n);
    }
    volatile u64 *l1 = (volatile u64 *)(l0[0] & ~0xfffULL);
    for (u64 a = pa & ~0xfffULL; a < pa + len; a += PAGE) {
        int i1 = (a >> 30) & 0x1ff, i2 = (a >> 21) & 0x1ff, i3 = (a >> 12) & 0x1ff;
        u64 e1 = l1[i1];
        if ((e1 & 3) == 1) {                                       /* 1GB block → private L2 of 2MB blocks */
            u64 n = unit_table_page(); if (!n) return -2;
            volatile u64 *t = (volatile u64 *)n; u64 base = e1 & ~0x3fffffffULL; u64 flags = e1 & 0xfffULL;
            for (int k = 0; k < 512; k++) t[k] = (base + ((u64)k << 21)) | flags;
            l1[i1] = PT_TABLE(n); e1 = l1[i1];
        } else if ((e1 & ~0xfffULL) == MMU_GB0 || (e1 & ~0xfffULL) == MMU_GB3) {   /* shared kernel L2 → clone */
            u64 n = unit_table_page(); if (!n) return -2;
            mcpy((void *)n, (void *)(e1 & ~0xfffULL), PAGE); l1[i1] = PT_TABLE(n); e1 = l1[i1];
        }
        volatile u64 *l2 = (volatile u64 *)(e1 & ~0xfffULL);
        u64 e2 = l2[i2];
        if ((e2 & 3) == 1) {                                       /* 2MB block → L3 of 4KB pages, same attrs */
            u64 n = unit_table_page(); if (!n) return -2;
            volatile u64 *t = (volatile u64 *)n; u64 base = e2 & ~0x1fffffULL; int battr = (e2 >> 2) & 7;
            for (int k = 0; k < 512; k++) t[k] = PT_PAGE_K(base + ((u64)k << 12), battr);
            l2[i2] = PT_TABLE(n); e2 = l2[i2];
        } else if (!(e2 & 1)) return -3;                           /* unmapped in the kernel: refuse */
        volatile u64 *l3 = (volatile u64 *)(e2 & ~0xfffULL);
        l3[i3] = PT_PAGE_U(a, attr);
    }
    unit_tables_clean();
    return 0;
}

/* Continue a sleeping cur-> Returns its exit/sleep code. */
static u64 unit_resume(void) {
    cur->sleeping = 0; unit_resumes++;
    timer_arm(SLICE_MS);
    u64 code = proc_resume(cur->ctx, cur->l0);
    timer_off();
    __asm__ volatile("msr ttbr0_el1, %0; isb" :: "r"((u64)MMU_L0)); __asm__ volatile("dsb sy; tlbi vmalle1is; dsb sy; isb");
    return code;
}

/* ── Preemption: GIC-400 + EL1 physical timer (PPI 30). IRQs are unmasked only
 * while a unit runs at EL0; the kernel itself is never interrupted. A slice
 * expiring lands in el0_irq_entry → el0_irq(): the unit is parked exactly
 * like a sleep(0) and the main loop gets a turn. A unit that never sleeps
 * voluntarily for UNIT_BUDGET_MS of CPU is killed as hung. ── */
#define GICD 0xFF841000ULL
#define GICC 0xFF842000ULL
#define TIMER_IRQ 30
static void gic_init(void) {
    wr32(GICD + 0x000, 0);
    wr32(GICD + 0x100, 1u << TIMER_IRQ);                                   /* ISENABLER0: PPI 30 */
    u32 pr = rd32(GICD + 0x400 + 28); pr = (pr & ~(0xffu << 16)) | (0xa0u << 16); wr32(GICD + 0x400 + 28, pr);
    wr32(GICD + 0x000, 1);
    wr32(GICC + 0x004, 0xf0);                                              /* PMR */
    wr32(GICC + 0x008, 0);                                                 /* BPR */
    wr32(GICC + 0x000, 1);                                                 /* CPU interface on */
}
static void timer_arm(u32 ms) {
    u64 t = timer_frq() * ms / 1000;
    __asm__ volatile("msr cntp_tval_el0, %0" :: "r"(t));
    __asm__ volatile("msr cntp_ctl_el0, %0" :: "r"(1ULL)); __asm__ volatile("isb");
}
static void timer_off(void) { __asm__ volatile("msr cntp_ctl_el0, %0" :: "r"(0ULL)); __asm__ volatile("isb"); }

void el0_irq(u64 *regs) {
    u32 iar = rd32(GICC + 0x00c); u32 id = iar & 0x3ff;
    if (id >= 1020) return;                                                /* spurious */
    wr32(GICC + 0x010, iar);                                               /* EOI */
    if (id != TIMER_IRQ) return;
    timer_off();
    unit_preempts++; cur->cpu_ms += SLICE_MS;
    if (cur->cpu_ms > cur->budget_ms) {
        unit_log("hung: no sleep for 3s of CPU — killed");
        cur->exit_code = UNIT_DEAD; cur->active = 0;
        if (cur->is_persona) api_emit(0xDEAD, regs[31]);
        __asm__ volatile("msr ttbr0_el1, %0; isb" :: "r"((u64)MMU_L0)); __asm__ volatile("dsb sy; tlbi vmalle1is; dsb sy; isb");
        proc_exit(UNIT_DEAD);
    }
    for (int i = 0; i < FRAME_WORDS; i++) cur->ctx[i] = regs[i];         /* park as-is (x0 kept) */
    cur->preempted = 1; cur->sleeping = 1; cur->wake_at = now_ms();
    __asm__ volatile("msr ttbr0_el1, %0; isb" :: "r"((u64)MMU_L0)); __asm__ volatile("dsb sy; tlbi vmalle1is; dsb sy; isb");
    proc_exit(UNIT_SLEEPING);
}

/* Run the unit to completion (phase 2: synchronous). Returns exit code. */
static u64 unit_run(void) {
    timer_arm(SLICE_MS);
    u64 code = proc_enter(cur->entry, cur->sp, cur->l0);
    timer_off();
    __asm__ volatile("msr ttbr0_el1, %0; isb" :: "r"((u64)MMU_L0)); __asm__ volatile("dsb sy; tlbi vmalle1is; dsb sy; isb");
    return code;
}

/* copy a NUL-terminated string from unit VA (already mapped in the current TTBR0) */
static int copy_user_str(char *dst, u64 uva, int max) {
    if (uva < UNIT_BASE || uva >= UNIT_BASE + 0x200000ULL) return -1;
    int n = 0; const char *p = (const char *)uva;
    while (n < max - 1 && p[n]) { dst[n] = p[n]; n++; }
    dst[n] = 0; return n;
}

static int user_ok(u64 uva, u64 len) { return uva >= UNIT_BASE && uva + len <= UNIT_BASE + 0x200000ULL; }

static u64 do_syscall(u64 nr, u64 a0, u64 a1, u64 a2, u64 a3, u64 a4, u64 *regs) {
    switch (nr) {
    case SYS_LOG: { char b[128]; if (copy_user_str(b, a0, sizeof b) < 0) return (u64)-1; unit_log(b); return 0; }
    case SYS_EXIT: cur->exit_code = a0; cur->active = 0;
        __asm__ volatile("msr ttbr0_el1, %0; isb" :: "r"((u64)MMU_L0)); __asm__ volatile("dsb sy; tlbi vmalle1is; dsb sy; isb");
        proc_exit(a0);
    case SYS_YIELD: return 0;
    case SYS_MBOX: {                                          /* platform mailbox on behalf of a driver process */
        if (a1 < 8 || a1 > 128 || !user_ok(a0, a1)) return (u64)-1;
        mcpy(mbox_buf, (void *)a0, (int)a1); dsb();
        int ok = mbox_call();
        mcpy((void *)a0, mbox_buf, (int)a1);
        return ok ? 0 : (u64)-2; }
    case SYS_TOUCH: touch_publish((int)a0, (int)a1, (int)a2); return 0;
    case SYS_WAIT:                                            /* park until resumed on purpose */
        for (int i = 0; i < FRAME_WORDS; i++) cur->ctx[i] = regs[i];
        cur->ctx[0] = 0;
        cur->preempted = 0; cur->sleeping = 1; cur->wake_at = ~0ULL; cur->cpu_ms = 0;
        __asm__ volatile("msr ttbr0_el1, %0; isb" :: "r"((u64)MMU_L0)); __asm__ volatile("dsb sy; tlbi vmalle1is; dsb sy; isb");
        proc_exit(UNIT_SLEEPING);
    case SYS_SLEEP:                                           /* park: save the frame, hand the CPU back */
        for (int i = 0; i < FRAME_WORDS; i++) cur->ctx[i] = regs[i];
        cur->ctx[0] = cur->stop_req ? 1 : 0;                  /* syscall result seen on resume */
        cur->preempted = 0;
        cur->sleeping = 1; cur->wake_at = now_ms() + (a0 ? a0 : 1); cur->cpu_ms = 0;
        __asm__ volatile("msr ttbr0_el1, %0; isb" :: "r"((u64)MMU_L0)); __asm__ volatile("dsb sy; tlbi vmalle1is; dsb sy; isb");
        proc_exit(UNIT_SLEEPING);
    /* api_t slots (same contracts as the in-kernel table) */
    case SYS_API + 0: fb_clear((u32)a0); return 0;
    case SYS_API + 1: fb_rect((int)a0, (int)a1, (int)a2, (int)a3, (u32)a4); return 0;
    case SYS_API + 2: { char b[160]; if (copy_user_str(b, a4, sizeof b) < 0) return (u64)-1; fb_text((int)a0, (int)a1, (int)a2, (u32)a3, b); return 0; }
    case SYS_API + 3: return now_ms();
    case SYS_API + 4: return wall_sec();
    case SYS_API + 5: api_gpio_out((u8)a0, (u8)a1); return 0;
    case SYS_API + 6: return gpio_read((u8)a0);
    case SYS_API + 7: return get_soc_temp();
    case SYS_API + 8: return api_param((int)a0);
    case SYS_API + 9: { int x = 0, y = 0; int r = api_touch(&x, &y);
        if (a0 && user_ok(a0, 4)) { *(volatile int *)a0 = x; }
        if (a1 && user_ok(a1, 4)) { *(volatile int *)a1 = y; }
        return (u64)r; }
    case SYS_API + 10: api_emit((u32)a0, a1); return 0;
    case SYS_API + 11: { char b[128]; if (copy_user_str(b, a0, sizeof b) < 0) return (u64)-1; api_log(b); return 0; }
    case SYS_API + 12: return api_screen();
    case SYS_API + 13: { int max = (int)a1; if (max < 0 || max > 512 || !user_ok(a0, (u64)max * 4)) return 0; return (u64)api_touch_trace((unsigned int *)a0, max); }
    default: return (u64)-38;   /* ENOSYS */
    }
    (void)regs; return 0;
}

/* EL0 sync exception: SVC → syscall; anything else → the unit dies, the kernel lives. */
void el0_sync(u64 esr, u64 far, u64 *regs) {
    u32 ec = (esr >> 26) & 0x3f;
    if (ec == 0x15) { regs[0] = do_syscall(regs[8], regs[0], regs[1], regs[2], regs[3], regs[4], regs); return; }
    char b[112]; int n = scpy(b, "unit fault ec=0x"); const char h[] = "0123456789abcdef";
    b[n++] = h[(ec >> 4) & 0xf]; b[n++] = h[ec & 0xf];
    n += scpy(b+n, " iss=0x"); for (int i = 20; i >= 0; i -= 4) b[n++] = h[(esr >> i) & 0xf];
    n += scpy(b+n, " far=0x");
    for (int i = 44; i >= 0; i -= 4) b[n++] = h[(far >> i) & 0xf];
    n += scpy(b+n, " elr=0x"); for (int i = 44; i >= 0; i -= 4) b[n++] = h[(regs[31] >> i) & 0xf];
    b[n] = 0; unit_log(b);
    cur->exit_code = UNIT_DEAD; cur->active = 0;
    if (cur->is_persona) api_emit(0xDEAD, regs[31]);
    __asm__ volatile("msr ttbr0_el1, %0; isb" :: "r"((u64)MMU_L0)); __asm__ volatile("dsb sy; tlbi vmalle1is; dsb sy; isb");
    proc_exit(UNIT_DEAD);
}

/* URUN: run the staged binary as an EL0 unit, reply with exit code + log. */
static void handle_urun(const u8 *p, int len) {
    u32 clen = stage_check(p, len, "URUN"); if (!clen) return;
    cur = &units[SLOT_URUN];
    if (cur->active) { poke_resp_str("{\"error\":\"busy\"}"); return; }
    int rc = unit_create(stage_buf, clen, 0);
    if (rc) { poke_resp_str(rc == -1 ? "{\"error\":\"too big\"}" : "{\"error\":\"no pages\"}"); return; }
    if (len > 8 && apply_mappings(p, len) < 0) { unit_destroy(); poke_resp_str("{\"error\":\"map failed\"}"); return; }
    u64 t0 = now_ms();
    u64 code = unit_run();
    while (code == UNIT_SLEEPING && now_ms() - t0 < 5000) { while (now_ms() < cur->wake_at) {} code = unit_resume(); }
    u64 dt = now_ms() - t0;
    char r[720]; int n = scpy(r, "{\"unit\":\"exited\",\"code\":"); n += idec((u32)code, r+n);
    n += scpy(r+n, ",\"ms\":"); n += idec((u32)dt, r+n);
    n += scpy(r+n, ",\"pages\":"); n += idec(cur->npages + 4, r+n);
    n += scpy(r+n, ",\"log\":\""); for (int i = 0; i < cur->loglen && n < 700; i++) { char c = cur->log[i]; if (c == '\n') { r[n++] = '\\'; r[n++] = 'n'; } else if (c == '"' || c == '\\') { r[n++] = '\\'; r[n++] = c; } else r[n++] = c; }
    r[n++] = '"'; r[n++] = '}';
    poke_resp((const u8 *)r, n);
    unit_destroy();
}

/* ── personas as EL0 units ── */
static int unit_persona_start(const u8 *code, int clen) {
    int has_ret = 0;
    for (int i = 0; i <= clen - 4; i += 4) {
        u32 insn = code[i] | (code[i+1]<<8) | (code[i+2]<<16) | ((u32)code[i+3]<<24);
        if (insn == 0xD65F03C0) { has_ret = 1; break; }
    }
    if (!has_ret) return -2;
    cur = &units[SLOT_PERSONA];
    if (cur->active) unit_destroy();
    persona_active = 0;
    int rc = unit_create(code, (u32)clen, 1);
    if (rc) { uprint("[UNIT] persona create failed\n"); return -1; }
    persona_fn = 0; persona_tick = 0; persona_last_ms = now_ms();
    persona_active = 1;                                      /* console yields the screen before tick 0 */
    u64 c = unit_run();                                       /* runs tick 0, parks in sleep() */
    if (c != UNIT_SLEEPING) { uprint("[UNIT] persona ended at tick 0\n"); unit_destroy(); persona_active = 0; return -1; }
    return 0;
}
static void unit_persona_stop(void) { cur = &units[SLOT_PERSONA]; if (cur->active && cur->is_persona) unit_destroy(); }

/* Scheduler: resume every parked unit whose wake time has come; one pass per
 * main-loop iteration, so the network and the console always get a turn. */
static void unit_sched(void) {
    for (int i = 0; i < UNIT_SLOTS; i++) {
        unit_t *u = &units[i];
        if (!u->active || !u->sleeping || now_ms() < u->wake_at) continue;
        cur = u;
        u64 c = unit_resume();
        if (c == UNIT_SLEEPING) continue;
        uprint("[UNIT] slot "); udec(i); uprint(" ended code="); udec((u32)c); uputc('\n');
        if (i == SLOT_PERSONA) {
            if (fb_ok) fb_text(40, 560, 2, 0x00FF4040, c == UNIT_DEAD ? "persona crashed — isolated, device alive" : "persona exited");
            persona_active = 0;
        }
        if (i == SLOT_RESIDENT) { tp_avail = 0; tp_down = 0; if (fb_ok) fb_text(40, 576, 2, 0x00FF4040, c == UNIT_DEAD ? "resident crashed — isolated, device alive" : "resident exited"); }
        unit_destroy();
    }
}
static void unit_persona_run(void) { unit_sched(); }

/* ── resident driver process ── */
static void resident_stop(void) {
    unit_t *u = &units[SLOT_RESIDENT];
    if (!u->active) return;
    cur = u; u->stop_req = 1;
    u64 t0 = now_ms(), c = UNIT_SLEEPING;                    /* wake it: its sleep() returns 1 → RES_STOP → exit */
    while (u->active && now_ms() - t0 < 3000) {
        if (!u->sleeping) break;
        c = unit_resume();
        if (c != UNIT_SLEEPING) break;
    }
    tp_avail = 0; tp_down = 0;
    unit_destroy();
    uprint("[UNIT] resident stopped\n");
}


/* apply a mapping list "count32 {pa64 len64 attr32}*" from a command payload; returns the name offset or -1 */
static int apply_mappings(const u8 *p, int len) {
    int o = 8; u32 nmap = 0;
    if (len >= 12) { nmap = ld32(p + 8); o = 12; }
    if (nmap > 16 || len < o + (int)nmap * 20) return -1;
    for (u32 i = 0; i < nmap; i++) {
        const u8 *m = p + o + i * 20;
        u64 pa = ld32(m) | ((u64)ld32(m + 4) << 32), ln = ld32(m + 8) | ((u64)ld32(m + 12) << 32); u32 attr = ld32(m + 16);
        if (unit_map(pa, ln, attr == 1 ? ATTR_NC : ATTR_DEV)) return -1;
    }
    return o + (int)nmap * 20;
}

static void handle_rsld(const u8 *p, int len) {
    u32 clen = stage_check(p, len, "RSLD"); if (!clen) return;
    resident_stop();
    cur = &units[SLOT_RESIDENT];
    int rc = unit_create(stage_buf, clen, 2);
    if (rc) { poke_resp_str(rc == -1 ? "{\"error\":\"too big\"}" : "{\"error\":\"no pages\"}"); return; }
    int o = apply_mappings(p, len);
    if (o < 0) { unit_destroy(); poke_resp_str("{\"error\":\"map failed\"}"); return; }
    {   /* service page: the hub's request/response channel to this driver */
        u64 pa = page_alloc();
        if (!pa) { unit_destroy(); poke_resp_str("{\"error\":\"no pages\"}"); return; }
        cur->pages[cur->npages++] = pa; cur->svc_pa = pa; mset((void *)pa, 0, PAGE);
        volatile u64 *l3 = (volatile u64 *)cur->l3;
        l3[(UNIT_SVC_VA >> 12) & 0x1ff] = PT_PAGE_USER(pa);
        unit_tables_clean();
    }
    { int k = 0; const u8 *nm = p + o; while (nm < (const u8 *)p + len && nm[k] && k < 31) { cur->name[k] = nm[k]; k++; } cur->name[k] = 0; }
    /* run INIT to completion: keep resuming while it is merely preempted */
    u64 t0 = now_ms(); u64 c = unit_run();
    while (c == UNIT_SLEEPING && cur->preempted && now_ms() - t0 < 15000) c = unit_resume();
    if (c == UNIT_SLEEPING && !cur->preempted) {              /* parked in its first voluntary sleep: INIT done */
        char r[96]; int n = scpy(r, "{\"resident\":\""); n += scpy(r+n, cur->name); n += scpy(r+n, "\",\"size\":"); n += idec(clen, r+n);
        n += scpy(r+n, ",\"pages\":"); n += idec(cur->npages + 4, r+n); r[n++] = '}';
        poke_resp((const u8 *)r, n);
        uprint("[POKE] RSLD "); uprint(cur->name); uprint(" (EL0)\n");
        return;
    }
    char r[200]; int n = scpy(r, "{\"error\":\"init failed\",\"code\":"); n += idec((u32)c, r+n); n += scpy(r+n, ",\"last_log\":\"");
    const char *l = last_logs[(last_log_i + 3) % 4]; for (int i = 0; l[i] && n < 190; i++) if (l[i] != '"') r[n++] = l[i];
    r[n++] = '"'; r[n++] = '}'; poke_resp((const u8 *)r, n);
    tp_avail = 0; unit_destroy();
}

/* SREQ: payload = op u32 | data. Hands a request to the resident through its
 * service page and drives the process until it answers (or dies, or stalls).
 * Reply: "SRSP" status u32 | data — or a JSON error. */
static void st32(u8 *p, u32 v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }
static void handle_sreq(const u8 *p, int len) {
    cur = &units[SLOT_RESIDENT];
    if (!cur->active || !cur->svc_pa) { poke_resp_str("{\"error\":\"no resident\"}"); return; }
    if (len < 4 || len - 4 > SVC_MAX) { poke_resp_str("{\"error\":\"bad request\"}"); return; }
    svc_page_t *sp = (svc_page_t *)cur->svc_pa;
    sp->op = ld32(p); sp->req_len = (u32)(len - 4); mcpy(sp->req, p + 4, len - 4);
    dsb(); sp->req_seq++; dsb();
    u64 t0 = now_ms(); u64 c = UNIT_SLEEPING;
    while (sp->rsp_seq != sp->req_seq && now_ms() - t0 < 5000) {
        if (!cur->sleeping) break;
        c = unit_resume();
        if (c != UNIT_SLEEPING) {                                  /* the driver died on this request */
            char r[160]; int n = scpy(r, "{\"error\":\"resident crashed\",\"code\":"); n += idec((u32)c, r + n); n += scpy(r + n, "}");
            poke_resp((const u8 *)r, n); tp_avail = 0; unit_destroy(); return;
        }
    }
    if (sp->rsp_seq != sp->req_seq) { poke_resp_str("{\"error\":\"resident did not answer\"}"); return; }
    u32 rl = sp->rsp_len > 1380 ? 1380 : sp->rsp_len;
    u8 r[1392]; r[0] = 'S'; r[1] = 'R'; r[2] = 'S'; r[3] = 'P'; st32(r + 4, sp->status); mcpy(r + 8, sp->rsp, rl);
    poke_resp(r, 8 + rl);
}

/* ═══════════════════════════════════════════
 * Motion: a generated skill runs as an EL0 unit (SLOT_SKILL) and is stepped
 * once per control tick. The hub (or, later, a servo resident) supplies the
 * body state; the kernel copies it into the skill's motion_io_t page, lets
 * the skill compute, and passes the targets through the safety filter
 * (docs/design/motion-safety.md) before they leave. The skill never sees a
 * motor: it only ever writes numbers into its own page.
 * ═══════════════════════════════════════════ */
#include "motion_io.h"
#define UNIT_IO_VA   (UNIT_BASE + 0x180000ULL)
#ifdef NO_ETH
#define MOTION_STEP_US 200000                 /* twin: QEMU's clock is host wall time, and the host
                                                 (running the simulator) can stall the vCPU for tens of ms */
#else
#define MOTION_STEP_US 20000                  /* a step that takes longer is a hung skill */
#endif
static u64 motion_io_pa = 0;
static struct {
    float prev[MOTION_MAX_JOINTS];
    float max_rate;                            /* rad/s */
    float max_tilt;                            /* rad */
    int   reset_prev, estop;
    u32   clamped, rate_limited, nonfinite, steps;
} msafe;

static motion_io_t *mio(void) { return (motion_io_t *)motion_io_pa; }
static float fabsk(float x) { return x < 0 ? -x : x; }

static void motion_reply_err(const char *e, u32 code) {
    char r[200]; int n = scpy(r, "{\"error\":\""); n += scpy(r + n, e); n += scpy(r + n, "\",\"code\":"); n += idec(code, r + n);
    n += scpy(r + n, ",\"log\":\"");
    for (int i = 0; i < cur->loglen && n < 190; i++) { char c = cur->log[i]; r[n++] = (c == '\n' || c == '"') ? ' ' : c; }
    r[n++] = '"'; r[n++] = '}'; poke_resp((const u8 *)r, n);
}

static void handle_msto(void) {
    cur = &units[SLOT_SKILL];
    if (cur->active) unit_destroy();
    motion_io_pa = 0;
    poke_resp_str("{\"skill\":\"stopped\"}");
}

/* MLOD: payload = len32 sum32 | njoints u32 | limit f32[n] | axis u32[n]; binary already staged */
static void handle_mlod(const u8 *p, int len) {
    u32 clen = stage_check(p, len, "MLOD"); if (!clen) return;
    if (len < 12) { poke_resp_str("{\"error\":\"no body\"}"); return; }
    u32 n = ld32(p + 8);
    if (n == 0 || n > MOTION_MAX_JOINTS || len < 12 + (int)n * 8) { poke_resp_str("{\"error\":\"bad body\"}"); return; }
    cur = &units[SLOT_SKILL];
    if (cur->active) unit_destroy();
    if (unit_create(stage_buf, clen, 0)) { poke_resp_str("{\"error\":\"no pages\"}"); return; }
    u64 pa = page_alloc();
    if (!pa) { unit_destroy(); poke_resp_str("{\"error\":\"no pages\"}"); return; }
    cur->pages[cur->npages++] = pa;
    volatile u64 *l3 = (volatile u64 *)cur->l3;
    l3[(UNIT_IO_VA >> 12) & 0x1ff] = PT_PAGE_USER(pa);
    unit_tables_clean();
    motion_io_pa = pa;
    motion_io_t *io = mio();
    io->njoints = (int)n;
    for (u32 j = 0; j < n; j++) {
        u32 lb = ld32(p + 12 + j * 4); float lim; mcpy(&lim, &lb, 4);
        io->limit[j] = (lim > 0 && lim < 3.2f) ? lim : 1.2f;
        io->axis[j] = (int)ld32(p + 12 + n * 4 + j * 4);
    }
    mset(&msafe, 0, sizeof msafe);
    msafe.max_rate = 6.0f; msafe.max_tilt = 1.396f; msafe.reset_prev = 1;
    cur->name[0] = 's'; cur->name[1] = 'k'; cur->name[2] = 'i'; cur->name[3] = 'l'; cur->name[4] = 'l'; cur->name[5] = 0;
    u64 c = unit_run();                                   /* crt parks itself in SYS_WAIT */
    if (c != UNIT_SLEEPING || cur->preempted) { motion_reply_err("skill did not reach its wait", (u32)c); unit_destroy(); motion_io_pa = 0; return; }
    char r[96]; int k = scpy(r, "{\"skill\":\"loaded\",\"joints\":"); k += idec(n, r + k);
    k += scpy(r + k, ",\"pages\":"); k += idec(cur->npages + 4, r + k); r[k++] = '}';
    poke_resp((const u8 *)r, k);
    uprint("[POKE] MLOD skill ("); udec(clen); uprint(" B) at EL0\n");
}

/* MATT: payload = param f32[8]. A new attempt: time restarts, safety re-arms. */
static void handle_matt(const u8 *p, int len) {
    cur = &units[SLOT_SKILL];
    if (!cur->active || !motion_io_pa) { poke_resp_str("{\"error\":\"no skill\"}"); return; }
    motion_io_t *io = mio();
    for (int i = 0; i < 8; i++) { float v = 0; if (len >= (i + 1) * 4) mcpy(&v, p + i * 4, 4); io->param[i] = v; }
    io->t = 0; io->tick = 0;
    msafe.reset_prev = 1; msafe.estop = 0; msafe.clamped = msafe.rate_limited = msafe.nonfinite = msafe.steps = 0;
    poke_resp_str("{\"attempt\":\"armed\"}");
}

/* MSTP: payload = t f32 | tick u32 | q f32[n] | imu f32[6]
 * reply  = "MOUT" | cmd f32[n] | clamped u32 | rate_limited u32 | nonfinite u32 | estop u32 | step_us u32 */
static void handle_mstp(const u8 *p, int len) {
    cur = &units[SLOT_SKILL];
    if (!cur->active || !motion_io_pa) { poke_resp_str("{\"error\":\"no skill\"}"); return; }
    motion_io_t *io = mio();
    int n = io->njoints;
    if (len < 8 + n * 4 + 24) { poke_resp_str("{\"error\":\"short step\"}"); return; }
    mcpy(&io->t, p, 4); io->tick = ld32(p + 4);
    for (int j = 0; j < n; j++) mcpy(&io->q[j], p + 8 + j * 4, 4);
    for (int i = 0; i < 6; i++) mcpy(&io->imu[i], p + 8 + n * 4 + i * 4, 4);
    if (msafe.reset_prev) { for (int j = 0; j < n; j++) msafe.prev[j] = io->q[j]; msafe.reset_prev = 0; }

    /* let the skill compute one step; it may be preempted, but must finish in time */
    u64 t0 = timer_cnt(), lim = timer_frq() / 1000000 * MOTION_STEP_US;
    u64 c = unit_resume();
    while (c == UNIT_SLEEPING && cur->preempted && timer_cnt() - t0 < lim) c = unit_resume();
    u32 step_us = (u32)((timer_cnt() - t0) * 1000000 / timer_frq());
    if (c != UNIT_SLEEPING) {                              /* crashed or exited: device stays up */
        motion_reply_err(c == UNIT_DEAD ? "skill crashed" : "skill exited", (u32)c);
        unit_destroy(); motion_io_pa = 0; return;
    }
    if (cur->preempted) {                                  /* watchdog */
        unit_log("watchdog: step took too long — skill discarded");
        motion_reply_err("skill hung", step_us);
        unit_destroy(); motion_io_pa = 0; return;
    }

    /* safety filter: the only path from a skill's numbers to a motor */
    float tilt = fabsk(io->imu[0]) > fabsk(io->imu[1]) ? fabsk(io->imu[0]) : fabsk(io->imu[1]);
    if (tilt > msafe.max_tilt) msafe.estop = 1;
    float step = msafe.max_rate / (float)MOTION_HZ;
    u8 r[4 + MOTION_MAX_JOINTS * 4 + 20]; r[0] = 'M'; r[1] = 'O'; r[2] = 'U'; r[3] = 'T';
    for (int j = 0; j < n; j++) {
        float x = io->cmd[j];
        if (!(x == x) || x > 1e6f || x < -1e6f) { msafe.nonfinite++; x = msafe.prev[j]; }
        if (msafe.estop) x = msafe.prev[j];
        else {
            float L = io->limit[j];
            if (x > L) { x = L; msafe.clamped++; } else if (x < -L) { x = -L; msafe.clamped++; }
            float lo = msafe.prev[j] - step, hi = msafe.prev[j] + step;
            if (x > hi) { x = hi; msafe.rate_limited++; } else if (x < lo) { x = lo; msafe.rate_limited++; }
        }
        msafe.prev[j] = x;
        mcpy(r + 4 + j * 4, &x, 4);
    }
    msafe.steps++;
    u32 tail[5] = { msafe.clamped, msafe.rate_limited, msafe.nonfinite, (u32)msafe.estop, step_us };
    mcpy(r + 4 + n * 4, tail, 20);
    poke_resp(r, 4 + n * 4 + 20);
}

void kernel_main(void) {
    /* Stage 1: ACT LED — fast blink = kernel alive */
    act_init();
    act_blink(5, 50);   /* 5× fast blink */

    uart_init();
    fb_init();
    print_banner();
    mmu_init();         /* identity map, caches on (screen shows each step) */
    pool_init();        /* page pool for EL0 units */
    gic_init();         /* timer slices for EL0 units */

    /* Stage 2: 2 slow blinks = UART done */
    act_blink(2, 300);

    /* SoC temperature */
    u32 t = get_soc_temp();
    uprint("[TEMP] SoC: "); udec(t / 1000); uputc('.'); udec((t % 1000) / 100); uprint("C\n");

#ifndef NO_ETH
    /* GENET + PHY */
    genet_init();
    act_blink(3, 200);  /* Stage 3: 3 blinks = GENET init done */

    phy_ok = (phy_setup() == 0);
    if (phy_ok) {
        phy_config(0);
        uprint("[PHY] watching for link...\n");
    } else {
        uprint("[NET] ethernet not available\n\n");
    }
#else
    uprint("[NET] disabled (QEMU twin build)\n");
    twin_uart_init();
    uprint("[TWIN] protocol on mini UART (QEMU serial 1)\n\n");
#endif

#ifndef NO_ETH
#endif

    uprint("poke-pi4> ");

    /* Main loop — ACT LED heartbeat */
    u64 last_hb = timer_cnt();
    u64 hb_interval = timer_frq();  /* 1 second */
    u64 last_phy = 0;
    u64 strategy_start = now_ms();
    int phy_mode = 0;
    int genet_on = 0;

    while (1) {
        /* PHY link state machine (every 500ms) */
        if (phy_ok && now_ms() - last_phy >= 500) {
            last_phy = now_ms();
            int speed = phy_link_speed();
            if (speed > 0 && !eth_up) {
                uprint("[PHY] link up "); udec(speed); uprint(" Mbps\n");
                if (!genet_on) { genet_enable(speed); genet_on = 1; }
                else {
                    u32 cmd = grd(U_CMD); cmd &= ~(3 << 2);
                    if (speed == 1000) cmd |= (2 << 2);
                    else if (speed == 100) cmd |= (1 << 2);
                    gwr(U_CMD, cmd);
                    eth_up = 1;
                }
                uprint("[NET] IP: "); uip(OUR_IP); uprint(":"); udec(POKE_PORT); uputc('\n');
                act_blink(10, 50);
            } else if (speed == 0 && eth_up) {
                uprint("[PHY] link down\n");
                eth_up = 0;
                strategy_start = now_ms();
            } else if (speed == 0 && !eth_up) {
                /* still down: rotate strategy every 8s, show regs every try */
                if (now_ms() - strategy_start >= 8000) {
                    strategy_start = now_ms();
                    phy_mode = (phy_mode + 1) & 3;
                    u16 bmsr = mdio_rd(1), lpa = mdio_rd(5);
                    uprint("[PHY] bmsr="); uhex32(bmsr);
                    uprint(" lpa="); uhex32(lpa); uputc('\n');
                    phy_config(phy_mode);
                }
            }
        }

        /* Poll ethernet */
        if (eth_up) {
            u8 *frame;
            int len = eth_rx(&frame);
            if (len != 0) {
                if (len > 0) process_frame(frame, len);
                eth_rx_done();
            }

            /* Probe hub with ARP every 10s (tests TX path) */
            static u64 last_probe = 0;
            if (now_ms() - last_probe >= 10000) {
                last_probe = now_ms();
                arp_request(0x0A000001);  /* 10.0.0.1 */
            }

            /* RX/TX ring diagnostics every 5s — silenced during CS probe */
            static u64 last_diag = 0;
            if (0 && now_ms() - last_diag >= 5000) {
                last_diag = now_ms();
                uprint("[RING] rx hw=");
                udec(grd(RING(G_RDMA, 16, R_PI)) & 0xFFFF);
                uprint(" sw="); udec(rx_ci & 0xFFFF);
                uprint(" tx="); udec(grd(RING(G_TDMA, 16, RT_CI)) & 0xFFFF);
                uprint("/"); udec(tx_pi & 0xFFFF);
                uprint(" mac_rx="); udec(grd(G_UMAC + 0x428));  /* MIB RX pkts */
                uprint(" bc="); udec(grd(G_UMAC + 0x434));      /* MIB RX broadcast */
                uprint(" st="); uhex32(grd(DMA_G(G_RDMA, D_STAT)));
                uputc('\n');
            }
        }

#ifdef NO_ETH
        {   /* twin transport: "POKE" len32 payload, one frame at a time */
            static u8 tb[1416]; static int tn = 0; int c;
            while ((c = twin_getc()) >= 0) {
                if (tn < 4 && c != "POKE"[tn]) { tn = 0; continue; }
                tb[tn++] = (u8)c;
                if (tn >= 8) {
                    u32 plen = ld32(tb + 4);
                    if (plen > sizeof tb - 8) { tn = 0; continue; }
                    if ((u32)tn == 8 + plen) { handle_poke(tb + 8, (int)plen); tn = 0; }
                }
            }
        }
#endif
        /* Resident driver tick (event-ring drains, sensor polls, ...) */

        /* Touch feedback when no persona owns the screen */
        if ((svc.touch || tp_avail) && !persona_active && fb_ok) {
            int tx, ty;
            if (api_touch(&tx, &ty)) fb_rect(tx - 3, ty - 3, 6, 6, 0x0000FF66);
        }

        /* Resident persona tick */
        persona_run();

        /* Console (UART) — simple echo */
        if (!(rd32(UART0 + 0x18) & (1 << 4))) {
            u8 c = rd32(UART0) & 0xFF;
            if (c == '\r' || c == '\n') {
                uprint("\npoke-pi4> ");
            }
        }

        /* Heartbeat: short blink every second */
        if (timer_cnt() - last_hb >= hb_interval) {
            act_on(); delay_us(50000); act_off();
            last_hb = timer_cnt();
        }
    }
}
