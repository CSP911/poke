// FRAG {"name": "emc1403", "use_when": "보드나 칩 내부 온도를 알고 싶을 때 · 원격 다이오드에 연결된 CPU/전원부 온도를 읽을 때 · 세 지점 온도를 한 번에 비교할 때 · 과열 여부를 확인할 때", "functions": ["static int emc1403_identify(unsigned addr)", "static int emc1403_read(unsigned addr, unsigned ch, long *mc)", "static int emc1403_read_all(unsigned addr, long *t0, long *t1, long *t2)"]}
#ifndef FRAG_EMC1403_H
#define FRAG_EMC1403_H

#include "i2c_bus.h"

/* EMC1403: byte registers, no auto-increment.
 * Reading the high byte latches the matching low byte.
 * Low byte bits 7:5 = 0.125 C steps.
 * Config 0x03 bit 2 = extended range (offset 64 C). */
static const i2c_u8 emc1403_hi_reg[3] = { 0x00, 0x01, 0x23 };
static const i2c_u8 emc1403_lo_reg[3] = { 0x29, 0x10, 0x24 };

static int emc1403_identify(unsigned addr)
{
    i2c_u8 mid = 0, pid = 0;
    int rc;
    i2c_init();
    rc = i2c_read(addr, 0xFE, &mid, 1);
    if (rc) return -rc;
    rc = i2c_read(addr, 0xFD, &pid, 1);
    if (rc) return -rc;
    if (mid == 0x5D && pid == 0x21) return 1;
    return 0;
}

static int emc1403_read(unsigned addr, unsigned ch, long *mc)
{
    i2c_u8 cfg = 0, hi = 0, lo = 0;
    long v;
    int rc;
    if (ch > 2 || !mc) return 1;
    i2c_init();
    rc = i2c_read(addr, 0x03, &cfg, 1);
    if (rc) return rc;
    rc = i2c_read(addr, emc1403_hi_reg[ch], &hi, 1);
    if (rc) return rc;
    rc = i2c_read(addr, emc1403_lo_reg[ch], &lo, 1);
    if (rc) return rc;
    v = (long)hi * 1000L + (long)((lo >> 5) & 0x07) * 125L;
    if (cfg & 0x04) v -= 64000L;
    *mc = v;
    return 0;
}

static int emc1403_read_all(unsigned addr, long *t0, long *t1, long *t2)
{
    int rc;
    rc = emc1403_read(addr, 0, t0);
    if (rc) return rc;
    rc = emc1403_read(addr, 1, t1);
    if (rc) return rc;
    return emc1403_read(addr, 2, t2);
}

#endif
