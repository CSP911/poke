/* I2C bus helpers for sentence ops on Pi 4 (bus 1) — header-only, no libc.
 * Since runtime V5 the op has no hardware window at all: each transfer is a runtime call
 * (poke_i2c_xfer, an svc), and the runtime does it only if the image's allow list (from the op's
 * MANIFEST, or the fragments it includes) lets this device / register be touched. Pins and the
 * bus clock are the runtime's (rt.c i2c_pins).
 *   i2c_init()                           nothing to do (kept so fragments need no change)
 *   i2c_write(addr, data, n)             0 ok, 1 bad request, 2 no ACK, 3 clock stretch, 4 never finished
 *   i2c_read(addr, reg, buf, n)          pointer byte then n bytes; reg < 0 = no pointer
 *   fmt_int / fmt_fixed / fmt_pad        decimal formatting into a char buffer (returns chars written) */
#ifndef POKE_I2C_BUS_H
#define POKE_I2C_BUS_H
typedef unsigned char i2c_u8; typedef unsigned int i2c_u32;
unsigned long poke_i2c_xfer(unsigned addr, int read, i2c_u8 *buf, unsigned n);   /* runtime call */
static void i2c_init(void) {}
static int i2c_xfer(i2c_u32 addr, int read, i2c_u8 *buf, i2c_u32 n) { return (int)poke_i2c_xfer(addr, read, buf, n); }
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
