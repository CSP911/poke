// FRAG {"name": "dps310", "use_when": "someone asks for the air pressure or a barometer reading · estimating altitude or height above sea level from pressure · checking the temperature at the board or in the room · watching for weather changes from rising or falling pressure", "functions": ["static int dps310_read(unsigned addr, long *temp_mc, long *press_pa)", "static int dps310_read_raw(unsigned addr, long *temp_raw, long *press_raw)", "static int dps310_compensate(unsigned addr, long temp_raw, long press_raw, long *temp_mc, long *press_pa)"]}
#ifndef FRAG_DPS310_H
#define FRAG_DPS310_H

#include "i2c_bus.h"

/* Infineon DPS310 barometric pressure + temperature sensor.
 * Registers: 0x00-02 PSR (24-bit signed), 0x03-05 TMP (24-bit signed),
 * 0x06 PRS_CFG, 0x07 TMP_CFG, 0x08 MEAS_CFG (7 COEF_RDY, 6 SENSOR_RDY,
 * 5 TMP_RDY, 4 PRS_RDY; mode 2-0: 1 = one pressure, 2 = one temperature),
 * 0x10-0x21 coefficients, 0x28 COEF_SRCE (bit 7 = temp sensor used for coefs).
 * Everything here uses 1x oversampling, so kT = kP = 524288 = 2^19. */

#define DPS310_POLLS 3000

#define DPS310_REG_PROD_ID   0x0D
#define DPS310_PROD_ID       0x10   /* REV_ID 1, PROD_ID 0 (DPS368 shares it) */
#define DPS310_REG_BOSCH_ID  0xD0   /* BMP180/BMP280/BME280 chip ID; 0xFF on DPS */

static int dps310__wait(unsigned addr, i2c_u8 mask)
{
    i2c_u8 st;
    int e, n;
    for (n = 0; n < DPS310_POLLS; n++) {
        e = i2c_read(addr, 0x08, &st, 1);
        if (e) return e;
        if ((st & mask) == mask) return 0;
    }
    return 4;
}

static int dps310__set(unsigned addr, i2c_u8 reg, i2c_u8 val)
{
    i2c_u8 w[2];
    w[0] = reg;
    w[1] = val;
    return i2c_write(addr, w, 2);
}

static long dps310__s24(const i2c_u8 *b)
{
    long v = ((long)b[0] << 16) | ((long)b[1] << 8) | (long)b[2];
    if (v & 0x800000L) v -= 0x1000000L;
    return v;
}

static long dps310__s16(const i2c_u8 *b)
{
    long v = ((long)b[0] << 8) | (long)b[1];
    if (v & 0x8000L) v -= 0x10000L;
    return v;
}

static int dps310_read_raw(unsigned addr, long *temp_raw, long *press_raw)
{
    i2c_u8 cfg[3], src, b[3];
    int e, fix;

    i2c_init();
    e = dps310__wait(addr, 0xC0);              /* COEF_RDY | SENSOR_RDY */
    if (e) return e;
    e = i2c_read(addr, 0x06, cfg, 3);          /* PRS_CFG, TMP_CFG, MEAS_CFG */
    if (e) return e;

    /* results only valid for our scale factor at 1x oversampling */
    fix = (cfg[0] & 0x0F) || (cfg[1] & 0x0F);
    if (fix) {
        e = dps310__set(addr, 0x06, 0x00);
        if (e) return e;
    }

    if (fix || !(cfg[2] & 0x20)) {
        e = i2c_read(addr, 0x28, &src, 1);
        if (e) return e;
        e = dps310__set(addr, 0x07, (i2c_u8)(src & 0x80));
        if (e) return e;
        e = dps310__set(addr, 0x08, 0x02);     /* one temperature measurement */
        if (e) return e;
        e = dps310__wait(addr, 0x20);
        if (e) return e;
    }
    e = i2c_read(addr, 0x03, b, 3);
    if (e) return e;
    *temp_raw = dps310__s24(b);

    if (fix || !(cfg[2] & 0x10)) {
        e = dps310__set(addr, 0x08, 0x01);     /* one pressure measurement */
        if (e) return e;
        e = dps310__wait(addr, 0x10);
        if (e) return e;
    }
    e = i2c_read(addr, 0x00, b, 3);
    if (e) return e;
    *press_raw = dps310__s24(b);
    return 0;
}

static int dps310_compensate(unsigned addr, long temp_raw, long press_raw,
                             long *temp_mc, long *press_pa)
{
    i2c_u8 k[18];
    int e;
    long c0, c1, c00, c10, c01, c11, c20, c21, c30;
    long long t, p, a, b, c, d, acc;

    i2c_init();
    e = dps310__wait(addr, 0x80);
    if (e) return e;
    e = i2c_read(addr, 0x10, k, 18);
    if (e) return e;

    c0 = ((long)k[0] << 4) | ((long)k[1] >> 4);
    if (c0 & 0x800L) c0 -= 0x1000L;
    c1 = ((long)(k[1] & 0x0F) << 8) | (long)k[2];
    if (c1 & 0x800L) c1 -= 0x1000L;
    c00 = ((long)k[3] << 12) | ((long)k[4] << 4) | ((long)k[5] >> 4);
    if (c00 & 0x80000L) c00 -= 0x100000L;
    c10 = ((long)(k[5] & 0x0F) << 16) | ((long)k[6] << 8) | (long)k[7];
    if (c10 & 0x80000L) c10 -= 0x100000L;
    c01 = dps310__s16(k + 8);
    c11 = dps310__s16(k + 10);
    c20 = dps310__s16(k + 12);
    c21 = dps310__s16(k + 14);
    c30 = dps310__s16(k + 16);

    t = temp_raw;   /* Tsc = t / 2^19 */
    p = press_raw;  /* Psc = p / 2^19 */

    /* T = c0/2 + c1*Tsc, in m°C */
    *temp_mc = (long)((long long)c0 * 500 + (((long long)c1 * 1000 * t + (1LL << 18)) >> 19));

    /* P accumulated in Pa * 256 */
    a = (long long)c20 * 524288 + p * c30;          /* (c20 + Psc*c30) * 2^19 */
    a >>= 8;                                        /* ... * 2^11 */
    b = (long long)c10 * 2048 + ((p * a) >> 19);    /* (c10 + Psc*(...)) * 2^11 */
    acc = (long long)c00 * 256 + ((p * b) >> 22);   /* Psc*(...) * 2^8 */
    acc += (t * c01) >> 11;                         /* Tsc*c01 * 2^8 */
    c = (long long)c11 * 524288 + p * c21;          /* (c11 + Psc*c21) * 2^19 */
    c >>= 8;                                        /* ... * 2^11 */
    d = (t * c) >> 19;                              /* Tsc*(...) * 2^11 */
    acc += (p * d) >> 22;                           /* Psc*Tsc*(...) * 2^8 */
    *press_pa = (long)((acc + 128) >> 8);
    return 0;
}

static int dps310_read(unsigned addr, long *temp_mc, long *press_pa)
{
    long tr, pr;
    int e = dps310_read_raw(addr, &tr, &pr);
    if (e) return e;
    return dps310_compensate(addr, tr, pr, temp_mc, press_pa);
}

/* 1 = DPS310 (product ID 0x10; DPS368 reports the same), 0 = some other part,
 * -1 = cannot tell. Read-only. Some boards (the bench one) read 0xFF at 0x0D:
 * then we can only rule things out (a Bosch chip ID at 0xD0, reserved MEAS_CFG
 * bit 3 set); if nothing contradicts DPS310 the answer is -1, not a guess. */
static int dps310_identify(unsigned addr)
{
    i2c_u8 id, other, st;

    i2c_init();
    if (i2c_read(addr, DPS310_REG_PROD_ID, &id, 1)) return -1;
    if (id == DPS310_PROD_ID) return 1;
    if (id != 0xFF) return 0;

    /* 0x0D unreadable as an ID: look for evidence of a different part */
    if (i2c_read(addr, DPS310_REG_BOSCH_ID, &other, 1)) return -1;
    if (other != 0xFF) return 0;               /* BMP180/280, BME280, ... */
    if (i2c_read(addr, 0x08, &st, 1)) return -1;
    if (st & 0x08) return 0;                   /* MEAS_CFG bit 3 is reserved 0 */
    return -1;                                 /* DPS-like layout, no ID to confirm */
}

#endif
