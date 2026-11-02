// FRAG {"name": "pca9554", "use_when": "read whether a button, switch or contact on an expander pin is high or low · turn a relay, LED or enable line on an expander pin on or off · check the level of all eight expander pins at once · confirm that an output pin really took the level it was set to", "functions": ["static int pca9554_read(unsigned addr, unsigned *levels)", "static int pca9554_set_pin(unsigned addr, unsigned pin, unsigned level)", "static int pca9554_identify(unsigned addr)"]}
#ifndef FRAG_PCA9554_H
#define FRAG_PCA9554_H

#include "i2c_bus.h"

/* PCA9554 / TCA9554 / PCA9534: reg 0 input, 1 output, 2 invert (1 = inverted),
   3 direction (1 = input). Only the low 2 bits of the register number count. */

#define PCA9554_REG_INPUT  0x00
#define PCA9554_REG_OUTPUT 0x01
#define PCA9554_REG_INVERT 0x02
#define PCA9554_REG_DIR    0x03

/* True level of P0..P7 (bit n = Pn, 1 = high). The input register applies the
   invert register, so XOR with it to get the real pin level. Only reads. */
static int pca9554_read(unsigned addr, unsigned *levels)
{
    i2c_u8 in = 0, inv = 0;
    int e;

    i2c_init();
    e = i2c_read(addr, PCA9554_REG_INPUT, &in, 1);
    if (e) return e;
    e = i2c_read(addr, PCA9554_REG_INVERT, &inv, 1);
    if (e) return e;
    *levels = ((unsigned)in ^ (unsigned)inv) & 0xFFu;
    return 0;
}

/* Make one pin an output and drive it. The output latch is set first, then
   the direction, so the pin never shows the old latch value as an output. */
static int pca9554_set_pin(unsigned addr, unsigned pin, unsigned level)
{
    i2c_u8 out = 0, dir = 0;
    i2c_u8 buf[2];
    i2c_u8 mask;
    int e;

    if (pin > 7u) return 1;
    mask = (i2c_u8)(1u << pin);

    i2c_init();
    e = i2c_read(addr, PCA9554_REG_OUTPUT, &out, 1);
    if (e) return e;
    e = i2c_read(addr, PCA9554_REG_DIR, &dir, 1);
    if (e) return e;

    if (level) out = (i2c_u8)(out | mask);
    else       out = (i2c_u8)(out & (i2c_u8)~mask);
    buf[0] = PCA9554_REG_OUTPUT;
    buf[1] = out;
    e = i2c_write(addr, buf, 2);
    if (e) return e;

    if (dir & mask) {
        buf[0] = PCA9554_REG_DIR;
        buf[1] = (i2c_u8)(dir & (i2c_u8)~mask);
        e = i2c_write(addr, buf, 2);
        if (e) return e;
    }
    return 0;
}

/* 1 if the part at addr behaves like this expander, 0 if not, negative on a
   bus error. No ID register, so check the register map using reads only:
   a 2-byte read repeats the same register, register n+4 aliases register n,
   and the four registers are not all one value (a part that answers 0xFF or
   any single byte everywhere is not this one). */
static int pca9554_identify(unsigned addr)
{
    i2c_u8 lo[4][2], hi[2];
    unsigned r;
    int e, same = 1;

    i2c_init();
    for (r = 0; r < 4u; r++) {
        e = i2c_read(addr, r, lo[r], 2);
        if (e) return e;
        if (lo[r][0] != lo[r][1]) return 0;
        if (lo[r][0] != lo[0][0]) same = 0;
    }
    if (same) return 0;
    for (r = 0; r < 4u; r++) {
        e = i2c_read(addr, r + 4u, hi, 2);
        if (e) return e;
        if (hi[0] != lo[r][0] || hi[1] != lo[r][0]) return 0;
    }
    return 1;
}

#endif /* FRAG_PCA9554_H */
