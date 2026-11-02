// FRAG {"name": "pca9555", "use_when": "read which of the 16 pins (buttons, switches, LED lines) are high or low right now · turn one LED or output pin on the expander on or off · check a single input line, e.g. whether a door contact is closed · find out whether the chip at an address really is a PCA9555/TCA9555 port expander", "functions": ["static int pca9555_read(unsigned addr, unsigned *pins)", "static int pca9555_set_pin(unsigned addr, unsigned pin, unsigned level)", "static int pca9555_identify(unsigned addr)"]}
#ifndef FRAG_PCA9555_H
#define FRAG_PCA9555_H

#include "i2c_bus.h"

/* PCA9555 / TCA9555 registers (8 bits each):
 * 0/1 input P0/P1, 2/3 output P0/P1, 4/5 polarity inversion, 6/7 config (1 = input).
 * Only single-register accesses here (one register per write or read), so the
 * code does not depend on how the chip steps between the two registers of a pair. */

static int pca9555_wr1(unsigned addr, unsigned reg, unsigned val)
{
    i2c_u8 w[2];
    w[0] = (i2c_u8)reg;
    w[1] = (i2c_u8)(val & 0xFFu);
    return i2c_write(addr, w, 2);
}

/* All 16 pin levels: bit i = P0.i (i 0-7), bit 8+i = P1.i. 1 = high. */
static int pca9555_read(unsigned addr, unsigned *pins)
{
    i2c_u8 b0, b1;
    int e;

    i2c_init();
    /* turn off polarity inversion so the bits are the real levels */
    e = pca9555_wr1(addr, 4, 0x00);
    if (e) return e;
    e = pca9555_wr1(addr, 5, 0x00);
    if (e) return e;
    e = i2c_read(addr, 0, &b0, 1);
    if (e) return e;
    e = i2c_read(addr, 1, &b1, 1);
    if (e) return e;
    *pins = (unsigned)b0 | ((unsigned)b1 << 8);
    return 0;
}

/* Pin 0-15 becomes an output at the given level; the other pins stay as they were. */
static int pca9555_set_pin(unsigned addr, unsigned pin, unsigned level)
{
    i2c_u8 v;
    unsigned port, bit;
    int e;

    if (pin > 15u) return 1;
    port = pin >> 3;
    bit = 1u << (pin & 7u);

    i2c_init();
    /* set the output latch first, so the pin comes up at the right level */
    e = i2c_read(addr, (int)(2u + port), &v, 1);
    if (e) return e;
    if (level) v = (i2c_u8)(v | bit);
    else       v = (i2c_u8)(v & ~bit);
    e = pca9555_wr1(addr, 2u + port, v);
    if (e) return e;

    /* then make it an output (config bit 0) */
    e = i2c_read(addr, (int)(6u + port), &v, 1);
    if (e) return e;
    v = (i2c_u8)(v & ~bit);
    return pca9555_wr1(addr, 6u + port, v);
}

/* 1 = PCA9555, 0 = not, -1 = cannot tell. Read only. */
static int pca9555_identify(unsigned addr)
{
    i2c_u8 r[6];   /* registers 2..7 */
    i2c_u8 in0;
    unsigned i;
    int e;

    i2c_init();
    e = i2c_read(addr, 0, &in0, 1);
    if (e) return (e == 2) ? 0 : -1;
    for (i = 0; i < 6u; i++) {
        e = i2c_read(addr, (int)(2u + i), &r[i], 1);
        if (e) return (e == 2) ? 0 : -1;
    }
    /* polarity registers come up 00 and are almost never changed */
    if (r[2] != 0x00 || r[3] != 0x00) return 0;
    /* power-on map: output FF FF, config FF FF */
    if (r[0] == 0xFF && r[1] == 0xFF && r[4] == 0xFF && r[5] == 0xFF) return 1;
    /* polarity fits, but outputs/config have been changed since power-on */
    return -1;
}

#endif /* FRAG_PCA9555_H */
