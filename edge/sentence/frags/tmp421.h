// FRAG {"name": "tmp421", "use_when": "보드(칩 자체) 온도가 몇 도인지 알고 싶을 때 · 원격 다이오드에 붙은 부품(CPU, 방열판 등) 온도를 재야 할 때 · 보드 온도와 원격 온도를 함께 보고해야 할 때 · 과열 여부를 확인하려고 현재 온도를 읽을 때", "functions": ["static int tmp421_read_mc(unsigned addr, unsigned channel, long *mc)", "static int tmp421_read_both(unsigned addr, long *local_mc, long *remote_mc)"]}
#ifndef FRAG_TMP421_H
#define FRAG_TMP421_H

#include "i2c_bus.h"

/* TMP421 registers */
#define TMP421_REG_LOCAL_HI   0x00
#define TMP421_REG_REMOTE_HI  0x01
#define TMP421_REG_CONFIG1    0x09
#define TMP421_REG_LOCAL_LO   0x10
#define TMP421_REG_REMOTE_LO  0x11
#define TMP421_CFG1_EXTENDED  0x04   /* range bit: 1 = -55..150 °C, offset 64 */

/* channel 0 = local (board), 1 = remote. Result in milli-°C. */
static int tmp421_read_mc(unsigned addr, unsigned channel, long *mc)
{
    i2c_u8 cfg[1];
    i2c_u8 hi[1];
    i2c_u8 lo[1];
    int hreg, lreg, rc;
    long whole, q;

    if (channel > 1 || !mc)
        return 1;
    hreg = channel ? TMP421_REG_REMOTE_HI : TMP421_REG_LOCAL_HI;
    lreg = channel ? TMP421_REG_REMOTE_LO : TMP421_REG_LOCAL_LO;

    i2c_init();
    rc = i2c_read(addr, TMP421_REG_CONFIG1, cfg, 1);
    if (rc) return rc;
    /* the part latches the low byte when the high byte is read: high first */
    rc = i2c_read(addr, hreg, hi, 1);
    if (rc) return rc;
    rc = i2c_read(addr, lreg, lo, 1);
    if (rc) return rc;

    whole = (long)hi[0];
    if (cfg[0] & TMP421_CFG1_EXTENDED)
        whole -= 64;
    /* fraction in bits 7:4, 1/16 °C each */
    q = whole * 16 + (long)((lo[0] >> 4) & 0x0F);
    /* q * 62.5, floored */
    if (q >= 0)
        *mc = (q * 625) / 10;
    else
        *mc = -(((-q) * 625 + 9) / 10);
    return 0;
}

static int tmp421_read_both(unsigned addr, long *local_mc, long *remote_mc)
{
    int rc;
    rc = tmp421_read_mc(addr, 0, local_mc);
    if (rc) return rc;
    return tmp421_read_mc(addr, 1, remote_mc);
}

/* Identification registers */
#define TMP421_REG_MANUF_ID   0xFE
#define TMP421_REG_DEVICE_ID  0xFF
#define TMP421_MANUF_ID_TI    0x55
#define TMP421_DEVICE_ID      0x21   /* TMP422 = 0x22, TMP423 = 0x23 */

/* 1 = TMP421 at addr, 0 = some other part, negative = bus error */
static int tmp421_identify(unsigned addr)
{
    i2c_u8 man[1];
    i2c_u8 dev[1];
    int rc;

    i2c_init();
    rc = i2c_read(addr, TMP421_REG_MANUF_ID, man, 1);
    if (rc) return rc < 0 ? rc : -1;
    rc = i2c_read(addr, TMP421_REG_DEVICE_ID, dev, 1);
    if (rc) return rc < 0 ? rc : -1;

    if (man[0] != TMP421_MANUF_ID_TI)
        return 0;
    if (dev[0] != TMP421_DEVICE_ID)
        return 0;
    return 1;
}

#endif /* FRAG_TMP421_H */
