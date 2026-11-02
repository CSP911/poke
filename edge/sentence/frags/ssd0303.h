// FRAG {"name": "ssd0303", "use_when": "show a short word or number on the little OLED screen · put a status message on the bench display · blank the OLED screen and turn it on · write a reading on the display so a person can see it", "functions": ["static int ssd0303_on(unsigned addr)", "static int ssd0303_text(unsigned addr, unsigned line, unsigned x, const char *s)"]}
#ifndef FRAG_SSD0303_H
#define FRAG_SSD0303_H

#include "i2c_bus.h"

/* Bench facts: every command byte needs its own 0x80 control byte (a 0x00-led write is ignored);
   pixel data streams after one 0x40. RAM is 132 columns x 8 pages; the 96x16 screen shows
   columns 36..131 of pages 0..1. Data byte = 8 vertical pixels, LSB on top. */
#define SSD0303_COL0 36

/* no mirror, display follows RAM, not inverted, start line 0, display on */
static const i2c_u8 ssd0303_init_seq[10] = {
    0x80, 0xA0, 0x80, 0xA4, 0x80, 0xA6, 0x80, 0x40, 0x80, 0xAF
};

/* 5x7 font, column bytes, LSB = top pixel. 0 = space, 1-10 = '0'-'9', 11-36 = 'A'-'Z'. */
static const i2c_u8 ssd0303_font[37][5] = {
    {0x00,0x00,0x00,0x00,0x00},
    {0x3E,0x51,0x49,0x45,0x3E}, {0x00,0x42,0x7F,0x40,0x00},
    {0x42,0x61,0x51,0x49,0x46}, {0x21,0x41,0x45,0x4B,0x31},
    {0x18,0x14,0x12,0x7F,0x10}, {0x27,0x45,0x45,0x45,0x39},
    {0x3C,0x4A,0x49,0x49,0x30}, {0x01,0x71,0x09,0x05,0x03},
    {0x36,0x49,0x49,0x49,0x36}, {0x06,0x49,0x49,0x29,0x1E},
    {0x7E,0x11,0x11,0x11,0x7E}, {0x7F,0x49,0x49,0x49,0x36},
    {0x3E,0x41,0x41,0x41,0x22}, {0x7F,0x41,0x41,0x22,0x1C},
    {0x7F,0x49,0x49,0x49,0x41}, {0x7F,0x09,0x09,0x09,0x01},
    {0x3E,0x41,0x49,0x49,0x7A}, {0x7F,0x08,0x08,0x08,0x7F},
    {0x00,0x41,0x7F,0x41,0x00}, {0x20,0x40,0x41,0x3F,0x01},
    {0x7F,0x08,0x14,0x22,0x41}, {0x7F,0x40,0x40,0x40,0x40},
    {0x7F,0x02,0x0C,0x02,0x7F}, {0x7F,0x04,0x08,0x10,0x7F},
    {0x3E,0x41,0x41,0x41,0x3E}, {0x7F,0x09,0x09,0x09,0x06},
    {0x3E,0x41,0x51,0x21,0x5E}, {0x7F,0x09,0x19,0x29,0x46},
    {0x46,0x49,0x49,0x49,0x31}, {0x01,0x01,0x7F,0x01,0x01},
    {0x3F,0x40,0x40,0x40,0x3F}, {0x1F,0x20,0x40,0x20,0x1F},
    {0x3F,0x40,0x38,0x40,0x3F}, {0x63,0x14,0x08,0x14,0x63},
    {0x07,0x08,0x70,0x08,0x07}, {0x61,0x51,0x49,0x45,0x43}
};

/* Point the RAM write pointer at (page, RAM column). */
static int ssd0303_goto(unsigned addr, unsigned page, unsigned col)
{
    i2c_u8 b[6];

    b[0] = 0x80;
    b[1] = (i2c_u8)(0xB0 | (page & 7));
    b[2] = 0x80;
    b[3] = (i2c_u8)(col & 0x0F);
    b[4] = 0x80;
    b[5] = (i2c_u8)(0x10 | ((col >> 4) & 0x0F));
    return i2c_write(addr, b, 6);
}

/* Blank all of RAM, then switch the display on. */
static int ssd0303_on(unsigned addr)
{
    i2c_u8 b[34];
    int r, i, p, k;

    i2c_init();
    for (p = 0; p < 8; p++) {
        r = ssd0303_goto(addr, (unsigned)p, 0);
        if (r) return r;
        b[0] = 0x40;
        for (i = 1; i < 34; i++)
            b[i] = 0x00;
        for (k = 0; k < 4; k++) {
            r = i2c_write(addr, b, 34);
            if (r) return r;
        }
    }
    for (i = 0; i < 10; i++)
        b[i] = ssd0303_init_seq[i];
    return i2c_write(addr, b, 10);
}

/* Draw s on line 0/1 starting at screen column x, 5 font columns + 1 blank per char. */
static int ssd0303_text(unsigned addr, unsigned line, unsigned x, const char *s)
{
    i2c_u8 b[7];
    int r, i;

    if (line > 1 || x > 95 || !s)
        return 1;
    i2c_init();
    r = ssd0303_goto(addr, line, SSD0303_COL0 + x);
    if (r) return r;

    for (; *s; s++) {
        unsigned c = (unsigned)(unsigned char)*s;
        unsigned g = 0;
        if (x + 5 > 96)
            break;
        if (c >= 'a' && c <= 'z')
            c = c - 'a' + 'A';
        if (c >= '0' && c <= '9')
            g = 1 + (c - '0');
        else if (c >= 'A' && c <= 'Z')
            g = 11 + (c - 'A');
        b[0] = 0x40;
        for (i = 0; i < 5; i++)
            b[1 + i] = ssd0303_font[g][i];
        b[6] = 0x00;
        r = i2c_write(addr, b, x + 6 > 96 ? 6 : 7);
        if (r) return r;
        x += 6;
    }
    return 0;
}

/* Cannot tell: the part is write-only over I2C. It has no manufacturer/device ID,
   no revision code and no status register (every bench read returns 0xFF), so
   nothing readable can confirm it. Always returns -1; never touches the bus. */
static int ssd0303_identify(unsigned addr)
{
    (void)addr;
    return -1;
}

#endif /* FRAG_SSD0303_H */
