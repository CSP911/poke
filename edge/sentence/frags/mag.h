// FRAG {"name": "mag", "use_when": "reading the magnetometer's raw X/Y/Z counts · getting a compass heading or checking which way the device faces · sensing a nearby magnet or a change in the magnetic field · driving an HMC5883L-compatible or LSM303DLHC magnetometer at I2C 0x1e", "functions": ["static int mag_read(unsigned addr, int *x, int *y, int *z)"]}
#ifndef FRAG_MAG_H
#define FRAG_MAG_H

#include "i2c_bus.h"

/* HMC5883L-register-compatible 3-axis magnetometer (e.g. LSM303DLHC mag, usually at 0x1e).
 * Identity: registers 0x0A..0x0C read 'H','4','3'.
 * 0x00 CRA  : 0x10 = 15 Hz, 1 sample, normal measurement
 * 0x01 CRB  : 0x20 = gain 1090 LSB/G
 * 0x02 MODE : bits[1:0] 00 = continuous, 01 = single, 1x = idle (default 0x03)
 * 0x03..0x08: data, big-endian signed 16-bit, register order X, Z, Y
 * 0x09 SR   : bit0 RDY
 */
#define MAG_REG_CRA   0x00
#define MAG_REG_CRB   0x01
#define MAG_REG_MODE  0x02
#define MAG_REG_DATA  0x03

static int mag_s16be(const i2c_u8 *b)
{
    int v = ((int)b[0] << 8) | (int)b[1];
    if (v & 0x8000) v -= 0x10000;
    return v;
}

static int mag_wreg(unsigned addr, i2c_u8 reg, i2c_u8 val)
{
    i2c_u8 t[2];
    t[0] = reg;
    t[1] = val;
    return i2c_write(addr, t, 2);
}

static int mag_read(unsigned addr, int *x, int *y, int *z)
{
    i2c_u8 b[6];
    int r, i;

    i2c_init();

    /* enter continuous measurement mode if the part is idle / single-shot */
    b[0] = 0;
    r = i2c_read(addr, MAG_REG_MODE, b, 1);
    if (r) return r;
    if ((b[0] & 0x03) != 0x00) {
        r = mag_wreg(addr, MAG_REG_CRA, 0x10);   /* 15 Hz, 1 sample, normal */
        if (r) return r;
        r = mag_wreg(addr, MAG_REG_CRB, 0x20);   /* gain 1090 LSB/G */
        if (r) return r;
        r = mag_wreg(addr, MAG_REG_MODE, 0x00);  /* continuous measurement */
        if (r) return r;
    }

    for (i = 0; i < 6; i++) b[i] = 0;
    r = i2c_read(addr, MAG_REG_DATA, b, 6);
    if (r) return r;

    /* register order: X, Z, Y */
    if (x) *x = mag_s16be(&b[0]);
    if (z) *z = mag_s16be(&b[2]);
    if (y) *y = mag_s16be(&b[4]);
    return 0;
}

#endif /* FRAG_MAG_H */
