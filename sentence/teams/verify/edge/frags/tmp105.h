// FRAG {"name": "tmp105", "use_when": "reading the room temperature from a TMP105/TMP75 on I2C · checking whether the sensor at 0x4a (or 0x48-0x4f) answers · getting temperature in milli-degrees C for a threshold or overheating check · converting the 12-bit temperature register into mC", "functions": ["static int tmp105_read_mC(unsigned addr, long *mC)"]}
#ifndef FRAG_TMP105_H
#define FRAG_TMP105_H

#include "i2c_bus.h"

/* TMP105 / TMP75 compatible digital temperature sensor.
 * Pointer register 0x00 = temperature, 2 bytes, MSB first, left-justified
 * two's complement; top 12 bits valid, 1/16 degC per LSB (the 9-bit power-on
 * default resolution has 0.5 degC steps and decodes the same way).
 * Example: 0x13 0x80 -> 19.5 degC.
 * 0x01 = config (1 byte, default 0x00), 0x02 = T_LOW (default 0x4B00, 75 C),
 * 0x03 = T_HIGH (default 0x5000, 80 C). */
#define TMP105_REG_TEMP   0x00
#define TMP105_REG_CONFIG 0x01
#define TMP105_REG_TLOW   0x02
#define TMP105_REG_THIGH  0x03

static int tmp105_read_mC(unsigned addr, long *mC)
{
    i2c_u8 buf[2];
    int r;
    short raw;
    long v;

    buf[0] = 0;
    buf[1] = 0;
    i2c_init();
    r = i2c_read(addr, TMP105_REG_TEMP, buf, 2);
    if (r != 0) return r;

    raw = (short)(((i2c_u32)buf[0] << 8) | (i2c_u32)buf[1]);
    v = (long)(raw >> 4);          /* 1/16 degC units */
    *mC = (v * 1000) / 16;
    return 0;
}

#endif /* FRAG_TMP105_H */
