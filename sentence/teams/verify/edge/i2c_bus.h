/* I2C bus helpers for residents on Pi 4 (BSC1 = GPIO 2/3) — header-only, no libc.
 * The includer maps BSC1 (0xFE804000) and GPIO (0xFE200000) as capabilities.
 *   i2c_init()                           pins to ALT0, 100 kHz
 *   i2c_write(addr, data, n)             0 ok, 2 no ACK, 3 clock stretch, 4 never finished
 *   i2c_read(addr, reg, buf, n)          pointer byte then n bytes; reg < 0 = no pointer
 *   fmt_int / fmt_fixed / fmt_pad        decimal formatting into a char buffer (returns chars written) */
#ifndef POKE_I2C_BUS_H
#define POKE_I2C_BUS_H
typedef unsigned char i2c_u8; typedef unsigned int i2c_u32;
#ifndef BSC_BASE
#define BSC_BASE 0xFE804000UL
#endif
#define I2C_R(off) (*(volatile i2c_u32 *)(BSC_BASE + (off)))
static void i2c_init(void) {
    volatile i2c_u32 *fsel0 = (volatile i2c_u32 *)0xFE200000UL, *pup0 = (volatile i2c_u32 *)0xFE2000E4UL;
    i2c_u32 f = *fsel0; f &= ~((7u << 6) | (7u << 9)); f |= (4u << 6) | (4u << 9); *fsel0 = f;
    i2c_u32 p = *pup0; p &= ~((3u << 4) | (3u << 6)); p |= (1u << 4) | (1u << 6); *pup0 = p;
    I2C_R(0x14) = 1500;
}
static int i2c_xfer(i2c_u32 addr, int read, i2c_u8 *buf, i2c_u32 n) {
    I2C_R(0x04) = 2u | (1u << 8) | (1u << 9);
    I2C_R(0x0C) = addr; I2C_R(0x08) = n;
    I2C_R(0x00) = (1u << 15) | (1u << 7) | (read ? (1u | (3u << 4)) : 0);
    i2c_u32 k = 0, st = 0;
    for (int i = 0; i < 20000; i++) {
        st = I2C_R(0x04);
        if (read) { while (k < n && (I2C_R(0x04) & (1u << 5))) buf[k++] = (i2c_u8)I2C_R(0x10); }
        else      { while (k < n && (I2C_R(0x04) & (1u << 4))) I2C_R(0x10) = buf[k++]; }
        if (st & ((1u << 8) | (1u << 9))) break;
        if ((st & 2u) && k == n) break;
        for (volatile int j = 0; j < 50; j++) {}
    }
    I2C_R(0x04) = 2u | (1u << 8) | (1u << 9);
    if (st & (1u << 8)) return 2;
    if (st & (1u << 9)) return 3;
    if (!(st & 2u)) return 4;
    return 0;
}
static int i2c_write(i2c_u32 addr, const i2c_u8 *data, i2c_u32 n) { i2c_u8 t[64]; if (n > 64) return 1; for (i2c_u32 i = 0; i < n; i++) t[i] = data[i]; return i2c_xfer(addr, 0, t, n); }
static int i2c_read(i2c_u32 addr, int reg, i2c_u8 *buf, i2c_u32 n) {
    if (reg >= 0) { i2c_u8 r = (i2c_u8)reg; int e = i2c_xfer(addr, 0, &r, 1); if (e) return e; }
    return i2c_xfer(addr, 1, buf, n);
}
static int fmt_int(char *out, long v) {
    char t[24]; int n = 0, k = 0; unsigned long u = v < 0 ? (unsigned long)(-v) : (unsigned long)v;
    if (v < 0) out[k++] = '-';
    if (!u) t[n++] = '0';
    while (u) { t[n++] = '0' + (char)(u % 10); u /= 10; }
    while (n) out[k++] = t[--n];
    return k;
}
/* value / 10^scale with `scale` decimals, e.g. fmt_fixed(out, 27500, 3) → "27.500" */
static int fmt_fixed(char *out, long v, int scale) {
    long p = 1; for (int i = 0; i < scale; i++) p *= 10;
    int k = 0; if (v < 0) { out[k++] = '-'; v = -v; }
    k += fmt_int(out + k, v / p);
    if (scale) { out[k++] = '.'; long f = v % p; for (long q = p / 10; q; q /= 10) out[k++] = '0' + (char)((f / q) % 10); }
    return k;
}
static int fmt_str(char *out, const char *s) { int k = 0; while (s[k]) { out[k] = s[k]; k++; } return k; }
/* zero-padded, e.g. fmt_pad(out, 7, 2) → "07", fmt_pad(out, 2026, 4) → "2026" */
static int fmt_pad(char *out, long v, int width) {
    char t[24]; int n = fmt_int(t, v), k = 0;
    for (int i = n; i < width; i++) out[k++] = '0';
    for (int i = 0; i < n; i++) out[k++] = t[i];
    return k;
}
#endif
