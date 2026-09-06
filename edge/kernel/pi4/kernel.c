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

static void fb_init(void) {
    mbox_buf[0] = 30 * 4;
    mbox_buf[1] = 0;
    mbox_buf[2] = 0x00048003; mbox_buf[3] = 8; mbox_buf[4] = 0;
    mbox_buf[5] = 800; mbox_buf[6] = 480;
    mbox_buf[7] = 0x00048004; mbox_buf[8] = 8; mbox_buf[9] = 0;
    mbox_buf[10] = 800; mbox_buf[11] = 480;
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
    if (!mbox_call()) return;
    if (!mbox_buf[23]) return;
    fb_base = (volatile u32 *)(u64)(mbox_buf[23] & 0x3FFFFFFF);
    fb_pitch = mbox_buf[28];
    fb_w = 800; fb_h = 480;
    fb_cx = 0; fb_cy = 0;
    for (u32 i = 0; i < fb_pitch * fb_h / 4; i++) fb_base[i] = 0;
    fb_ok = 1;
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

typedef struct {
    void (*clear)(u32 color);                              /* +0x00 */
    void (*rect)(int x, int y, int w, int h, u32 color);   /* +0x08 */
    void (*text)(int x, int y, int scale, u32 color, const char *s); /* +0x10 */
    u64  (*ms)(void);                                      /* +0x18 ms since boot */
    u64  (*clock)(void);                                   /* +0x20 epoch sec, 0=unset */
    void (*gpio_out)(u8 pin, u8 val);                      /* +0x28 */
    u8   (*gpio_in)(u8 pin);                               /* +0x30 */
    u32  (*temp_mc)(void);                                 /* +0x38 SoC temp milli-C */
} api_t;

static const api_t persona_api = {
    fb_clear, fb_rect, fb_text, now_ms, wall_sec,
    api_gpio_out, gpio_read, get_soc_temp,
};

/* ── Persona Slot (resident binary, called every tick) ── */
#define PERSONA_SZ 8192
#define PERSONA_TICK_MS 50
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
#define POKE_PORT   5555

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
static int eth_up = 0;
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
    uprint("[ARP] reply\n");
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
    uprint("[ICMP] reply\n");
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

static u8 peer_mac[6];
static u32 peer_ip;
static u16 peer_port;

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

static void handle_exec(const u8 *code, int clen) {
    if (clen <= 0 || clen > CODE_SZ) { poke_resp_str("error: size"); return; }
    mcpy(code_buf, code, clen);

    int has_ret = 0;
    for (int i = 0; i <= clen - 4; i += 4) {
        u32 insn = code_buf[i] | (code_buf[i+1]<<8) | (code_buf[i+2]<<16) | (code_buf[i+3]<<24);
        if (insn == 0xD65F03C0) { has_ret = 1; break; }
    }

    mset(res_buf, 0, 256);
    res_len = 0;
    u64 ret = 0;

    if (has_ret) {
        __asm__ volatile("dc civac, %0" :: "r"(code_buf));
        __asm__ volatile("dsb sy"); __asm__ volatile("ic ivau, %0" :: "r"(code_buf));
        __asm__ volatile("dsb sy"); __asm__ volatile("isb");
        u64 (*fn)(char *, int *) = (u64 (*)(char *, int *))code_buf;
        ret = fn(res_buf, &res_len);
    }

    char rsp[128]; int rl = 0;
    if (!has_ret) { rl = scpy(rsp, "no RET"); }
    else if (res_len > 0) { mcpy(rsp, res_buf, res_len); rl = res_len; }
    else { rl = scpy(rsp, "x0="); rl += idec((u32)ret, rsp + rl); }
    poke_resp((const u8 *)rsp, rl);
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
        n += scpy(r+n, ",\"commands\":[\"PING\",\"INFO\",\"EXEC\",\"GPIO\",\"GPOS\",\"TEMP\",\"DRAW\",\"PRUN\",\"PSTP\",\"TIME\"]");
        n += scpy(r+n, ",\"display\":"); n += scpy(r+n, fb_ok ? "\"800x480\"" : "null");
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
        uprint("[POKE] PSTP\n");
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
        else if (ntohs(a->oper) == 2) {
            uprint("[ARP] peer ");
            u32 spa; mcpy(&spa, a->spa, 4);
            uip(ntohl(spa)); uprint(" is alive\n");
        }
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

void kernel_main(void) {
    /* Stage 1: ACT LED — fast blink = kernel alive */
    act_init();
    act_blink(5, 50);   /* 5× fast blink */

    uart_init();
    fb_init();
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
    uprint("[TWIN] starting demo persona\n\n");
    persona_fn = demo_persona;
    persona_tick = 0;
    persona_last_ms = now_ms();
    persona_active = 1;
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

            /* RX/TX ring diagnostics every 5s */
            static u64 last_diag = 0;
            if (now_ms() - last_diag >= 5000) {
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
