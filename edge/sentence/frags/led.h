// FRAG {"name": "led", "use_when": "turn an LED on the PCA9552 expander on or off · make an LED blink at PWM0 or PWM1 rate · read which of the 16 expander pins are high or low right now · check a button wired to a PCA9552 pin", "functions": ["static int led_set(unsigned addr, int led, int mode)", "static int led_inputs(unsigned addr, unsigned *pins)"]}
#ifndef FRAG_LED_H
#define FRAG_LED_H

#include "i2c_bus.h"

/* PCA9552 16-bit I2C LED driver / GPIO expander.
 * Register map (verified): 0x00 INPUT0, 0x01 INPUT1, 0x02 PSC0, 0x03 PWM0,
 * 0x04 PSC1, 0x05 PWM1, 0x06..0x09 LS0..LS3.
 * Bit 0x10 in the register pointer enables auto-increment.
 * LSn: 2 bits per LED, LED (4n+k) at bits [2k+1:2k]:
 *   00 = output low (LED on), 01 = high-Z (LED off),
 *   10 = blink at PWM0/PSC0,  11 = blink at PWM1/PSC1. */
#define LED_PCA_AI      0x10
#define LED_REG_INPUT0  0x00
#define LED_REG_LS0     0x06

static int led_set(unsigned addr, int led, int mode)
{
    i2c_u8 cur;
    i2c_u8 wb[2];
    int idx, shift, rc;

    if (led < 0 || led > 15) return 1;
    if (mode < 0 || mode > 3) return 1;

    i2c_init();

    idx = led >> 2;
    shift = (led & 3) * 2;

    cur = 0;
    rc = i2c_read(addr, LED_REG_LS0 + idx, &cur, 1);
    if (rc != 0) return rc;

    cur = (i2c_u8)((cur & ~(3u << shift)) | ((unsigned)mode << shift));

    wb[0] = (i2c_u8)(LED_REG_LS0 + idx);
    wb[1] = cur;
    rc = i2c_write(addr, wb, 2);
    if (rc != 0) return rc;
    return 0;
}

static int led_inputs(unsigned addr, unsigned *pins)
{
    i2c_u8 in[2];
    int rc;

    if (!pins) return 1;

    i2c_init();

    in[0] = 0;
    in[1] = 0;
    rc = i2c_read(addr, LED_PCA_AI | LED_REG_INPUT0, in, 2);
    if (rc != 0) return rc;

    *pins = (unsigned)in[0] | ((unsigned)in[1] << 8);
    return 0;
}

#endif /* FRAG_LED_H */
