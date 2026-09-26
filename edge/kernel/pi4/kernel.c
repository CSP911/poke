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
 * XPT2046 Touch Controller (SPI0)
 * T_CLK=GPIO11 T_MOSI=GPIO10 T_MISO=GPIO9
 * T_CS=CE0/CE1 (auto-detect) T_IRQ=GPIO25
 * ═══════════════════════════════════════════ */
#define SPI0    (PERI + 0x204000)
#define PEN_IRQ 25

/* Current panel's touch film is cracked — its Z1 pressure reading floats
 * high permanently, so pressure detection fires ghost touches. Set to 1
 * when an intact XPT2046 panel is installed. */
#define TOUCH_ENABLED 0

/* raw→screen calibration (tune after corner test) */
#define TC_MIN   200
#define TC_MAX   3900

static int touch_cs = 1;        /* this panel wires T_CS to CE1 (verified) */
static int touch_down_f = 0;
static int touch_new = 0;
static int touch_sx = 0, touch_sy = 0;
static u32 touch_rx = 0, touch_ry = 0;

static void touch_init(void) {
    /* GPIO 7-11 → ALT0 (SPI0: CE1, CE0, MISO, MOSI, SCLK) */
    for (int pin = 7; pin <= 11; pin++) {
        u32 reg = rd32(GPIO_FSEL(pin));
        u32 shift = (pin % 10) * 3;
        reg &= ~(7u << shift);
        reg |= (4u << shift);          /* ALT0 */
        wr32(GPIO_FSEL(pin), reg);
    }
    /* GPIO25 input + pull-up (PENIRQ is active low) */
    u32 reg = rd32(GPIO_FSEL(PEN_IRQ));
    reg &= ~(7u << ((PEN_IRQ % 10) * 3));
    wr32(GPIO_FSEL(PEN_IRQ), reg);
    u32 pull = rd32(GPIO + 0xE8);      /* PUP_PDN reg1: pins 16-31 */
    pull &= ~(3u << ((PEN_IRQ - 16) * 2));
    pull |= (1u << ((PEN_IRQ - 16) * 2));  /* 01 = pull-up */
    wr32(GPIO + 0xE8, pull);
}

static u8 spi_byte(u8 out) {
    int t = 100000;
    while (!(rd32(SPI0 + 0x00) & (1 << 18)) && --t) { }  /* TXD */
    wr32(SPI0 + 0x04, out);
    t = 100000;
    while (!(rd32(SPI0 + 0x00) & (1 << 17)) && --t) { }  /* RXD */
    return rd32(SPI0 + 0x04) & 0xFF;
}

static u16 xpt_read(u8 cmd) {
    u32 cs = (3 << 4) | (u32)touch_cs;      /* clear FIFOs + chip select */
    wr32(SPI0 + 0x00, cs);
    wr32(SPI0 + 0x08, 2048);                /* slow, safe clock */
    wr32(SPI0 + 0x00, cs | (1 << 7));       /* TA: transfer active */
    spi_byte(cmd);
    u8 h = spi_byte(0), l = spi_byte(0);
    int t = 100000;
    while (!(rd32(SPI0 + 0x00) & (1 << 16)) && --t) { }  /* DONE */
    wr32(SPI0 + 0x00, (u32)touch_cs);       /* TA off */
    return (u16)((((h << 8) | l) >> 3) & 0xFFF);
}

static u16 med3(u16 a, u16 b, u16 c) {
    if (a > b) { u16 t = a; a = b; b = t; }
    if (b > c) { u16 t = b; b = c; c = t; }
    if (a > b) { u16 t = a; a = b; b = t; }
    return b;
}

static int clampi(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }

static void touch_poll(void) {
    /* pressure-based detection (Z1) — PENIRQ wiring varies across panels */
    u16 z1 = xpt_read(0xB0);
    if (z1 < 80) {                          /* no pressure = not touched */
        if (touch_down_f) { touch_down_f = 0; uprint("[TOUCH] up\n"); }
        return;
    }
    u32 rx = med3(xpt_read(0xD0), xpt_read(0xD0), xpt_read(0xD0));
    u32 ry = med3(xpt_read(0x90), xpt_read(0x90), xpt_read(0x90));

    if (rx < 30 || rx > 4070 || ry < 30 || ry > 4070) return;
    touch_rx = rx; touch_ry = ry;
    touch_sx = clampi((int)(rx - TC_MIN) * 800 / (TC_MAX - TC_MIN), 0, 799);
    touch_sy = clampi((int)(ry - TC_MIN) * 480 / (TC_MAX - TC_MIN), 0, 479);

    if (!touch_down_f) {
        touch_down_f = 1;
        touch_new = 1;    /* press edge — consumed by api_touch */
        uprint("[TOUCH] raw="); udec(rx); uputc(','); udec(ry);
        uprint(" scr="); udec(touch_sx); uputc(','); udec(touch_sy); uputc('\n');
    }
}

/* returns 0 = not pressed, 1 = held, 2 = new tap (once per press) */
static int api_touch(int *x, int *y) {
    if (x) *x = touch_sx;
    if (y) *y = touch_sy;
    if (!touch_down_f) return 0;
    if (touch_new) { touch_new = 0; return 2; }
    return 1;
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

#include "poke_api.h"

static const api_t persona_api = {
    fb_clear, fb_rect, fb_text, now_ms, wall_sec,
    api_gpio_out, gpio_read, get_soc_temp, api_param, api_touch, api_emit,
};

/* ── Persona Slot (resident binary, called every tick) ── */
#define PERSONA_SZ 8192
static u8 persona_buf[PERSONA_SZ] __attribute__((aligned(4096)));
static u64 persona_tick = 0;
static u64 persona_last_ms = 0;

typedef u64 (*persona_fn_t)(const api_t *api, u64 tick);
static persona_fn_t persona_fn = 0;

static void persona_stop(void) {
    persona_active = 0;
    persona_fn = 0;
    mset(persona_buf, 0, PERSONA_SZ);  /* volatile: discard on stop */
}

static int persona_load(const u8 *code, int clen) {
    if (clen <= 0 || clen > PERSONA_SZ) return -1;

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

static void poke_resp(const u8 *data, int len) {
    u8 rbuf[1400];
    rbuf[0]='R'; rbuf[1]='E'; rbuf[2]='S'; rbuf[3]='P';
    rbuf[4]=len&0xFF; rbuf[5]=(len>>8)&0xFF; rbuf[6]=(len>>16)&0xFF; rbuf[7]=(len>>24)&0xFF;
    if (len > 0 && len <= 1392) mcpy(rbuf + 8, data, len);
    send_udp(peer_mac, peer_ip, peer_port, POKE_PORT, rbuf, 8 + len);
}

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

/* EXLD: stage a chunk into code_buf at an offset. Payload = off(4 LE) + bytes.
 * Lets a probe larger than one MTU frame be uploaded in pieces (no reliance on
 * IP fragment reassembly, which the bare-metal stack does not do). */
static void handle_exld(const u8 *p, int len) {
    if (len < 4) { poke_resp_str("error: exld short"); return; }
    u32 off = p[0] | (p[1]<<8) | (p[2]<<16) | ((u32)p[3]<<24);
    int nb = len - 4;
    if (off > CODE_SZ || nb < 0 || off + (u32)nb > CODE_SZ) { poke_resp_str("error: exld range"); return; }
    mcpy(code_buf + off, p + 4, nb);
    char r[24]; int n = scpy(r, "ok "); n += idec(off + (u32)nb, r + n);
    poke_resp((const u8 *)r, n);
}

/* EXRN: run the code_buf staged by prior EXLD chunks. Payload = total_len(4 LE). */
static void handle_exrn(const u8 *p, int len) {
    if (len < 4) { poke_resp_str("error: exrn short"); return; }
    u32 clen = p[0] | (p[1]<<8) | (p[2]<<16) | ((u32)p[3]<<24);
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

/* USB HID touch state (defined in the USB module below; INFO reports it) */
static int usb_touch_ok;
static u32 usb_touch_vid, usb_touch_pid, usb_touch_reports;
static int uhex16s(u32 v, char *b);

/* ── POKE Command Router ── */
static void handle_poke(const u8 *payload, int len) {
    if (len < 4) { poke_resp_str("error: short"); return; }

    if (mcmp(payload, "PING", 4) == 0) {
        poke_resp_str("PONG");
        uprint("[POKE] PING\n");
    }
    else if (mcmp(payload, "INFO", 4) == 0) {
        u32 temp = get_soc_temp();
        char r[384]; int n = 0;
        n += scpy(r+n, "{\"status\":\"alive\",\"arch\":\"aarch64\",\"chip\":\"bcm2711\"");
        n += scpy(r+n, ",\"kernel\":\"poke-os\",\"transport\":\"udp\"");
        n += scpy(r+n, ",\"ip\":\"10.0.0.2\",\"port\":5555");
        n += scpy(r+n, ",\"commands\":[\"PING\",\"INFO\",\"EXEC\",\"EXLD\",\"EXRN\",\"GPIO\",\"GPOS\",\"TEMP\",\"DRAW\",\"PRUN\",\"PSTP\",\"PPAR\",\"TIME\"]");
        if (fb_ok) {
            n += scpy(r+n, ",\"display\":\""); n += idec(fb_w, r+n); r[n++] = 'x'; n += idec(fb_h, r+n); r[n++] = '"';
            n += scpy(r+n, ",\"fb_base\":"); n += idec((u32)(u64)fb_base, r+n);
            n += scpy(r+n, ",\"fb_pitch\":"); n += idec(fb_pitch, r+n);
        } else {
            n += scpy(r+n, ",\"display\":null");
        }
        n += scpy(r+n, ",\"touch\":"); n += scpy(r+n, usb_touch_ok ? "true" : "false");
        if (usb_touch_ok) {
            n += scpy(r+n, ",\"touch_dev\":\""); n += uhex16s(usb_touch_vid, r+n); r[n++] = ':'; n += uhex16s(usb_touch_pid, r+n); r[n++] = '"';
            n += scpy(r+n, ",\"touch_reports\":"); n += idec(usb_touch_reports, r+n);
        }
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

#ifdef NO_ETH
/* QEMU twin build: no GENET — run a built-in demo persona
 * through the exact same api_t/tick path a hub-generated
 * binary would use. */
static u64 demo_persona(const api_t *api, u64 tick) {
    if (tick == 0) {
        api->clear(0x00101828);
        api->text(64, 40, 8, 0x0000FF66, "POKE");
        api->text(64, 120, 2, 0x00AAAAAA, "prompt appliance demo");
    }
    u64 s = api->ms() / 1000;
    char b[16]; int n = scpy(b, "T+"); n += idec((u32)s, b + n); b[n++] = 's'; b[n] = 0;
    api->rect(64, 200, 400, 60, 0x00101828);
    api->text(64, 200, 6, 0x00FFFFFF, b);
    api->rect(64, 300, 672, 24, 0x00202838);
    api->rect(64, 300, (tick % 84) * 8, 24, 0x0000FF66);
    return 0;
}
#endif

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

void kernel_main(void) {
    /* Stage 1: ACT LED — fast blink = kernel alive */
    act_init();
    act_blink(5, 50);   /* 5× fast blink */

    uart_init();
    fb_init();
    print_banner();

    /* Stage 2: 2 slow blinks = UART done */
    act_blink(2, 300);

    /* SoC temperature */
    u32 t = get_soc_temp();
    uprint("[TEMP] SoC: "); udec(t / 1000); uputc('.'); udec((t % 1000) / 100); uprint("C\n");

    /* Touch controller */
    touch_init();
    uprint("[TOUCH] XPT2046 on SPI0, irq=GPIO25\n");

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
    uprint("[TWIN] starting demo persona\n\n");
    persona_fn = demo_persona;
    persona_tick = 0;
    persona_last_ms = now_ms();
    persona_active = 1;
#endif

#ifndef NO_ETH
    /* Bare-metal USB: PCIe root complex + VL805 xHCI */
    usb_init();
    usb_enumerate();    /* hub → HID touch; bounded, boot continues without touch on failure */
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

        /* Touch poll (every 20ms) */
        static u64 last_touch = 0;
        if (usb_touch_ok) {
            usb_touch_poll();               /* USB HID touch: cheap event-ring drain */
        } else if (TOUCH_ENABLED && now_ms() - last_touch >= 20) {
            last_touch = now_ms();
            touch_poll();                   /* legacy XPT2046 over SPI */
        }
        if (touch_down_f && !persona_active && fb_ok)
            fb_rect(touch_sx - 3, touch_sy - 3, 6, 6, 0x0000FF66);

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
