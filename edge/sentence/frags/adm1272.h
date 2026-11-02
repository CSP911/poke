// FRAG {"name": "adm1272", "use_when": "보드 입력 전압이 몇 V인지 물을 때 · 부하에 흐르는 전류(A)를 확인할 때 · 지금 소비 전력(W)을 알고 싶을 때 · 출력 전압이 떨어졌는지 점검할 때 · 전원 레일에 붙은 PMBus 칩이 ADM1272인지 확인할 때", "functions": ["static int adm1272_identify(unsigned addr)", "static int adm1272_read_word(unsigned addr, unsigned cmd, unsigned *raw)", "static int adm1272_read_vi(unsigned addr, unsigned long rs_uohm, long *vin_mv, long *vout_mv, long *iout_ma)", "static int adm1272_read_pin(unsigned addr, unsigned long rs_uohm, long *pin_mw)"]}
#ifndef FRAG_ADM1272_H
#define FRAG_ADM1272_H

#include "i2c_bus.h"

/* ADM1272 PMBus hot-swap controller / power monitor.
 * Direct format: X = (Y*10^-R - b) / m
 *   V: m = 4062 (100 V range) / 6770 (60 V range), b = 0, R = -2
 *   I: m = 663 (30 mV) / 1326 (15 mV) * Rs[mOhm], b = 20480, R = -1
 *   P: m = 10535 / 21071 / 17561 / 34474 * Rs[mOhm], b = 0, R = -3
 * Ranges come from PMON_CONFIG 0xD4: bit5 VRANGE (1 = 100 V), bit0 IRANGE (1 = 30 mV).
 * Block commands must be read whole: bytes left unread stay queued and
 * come out at the start of the next read. */

#define ADM1272_MFR_ID      0x99
#define ADM1272_MFR_MODEL   0x9A
#define ADM1272_READ_VIN    0x88
#define ADM1272_READ_VOUT   0x8B
#define ADM1272_READ_IOUT   0x8C
#define ADM1272_READ_PIN    0x97
#define ADM1272_PMON_CONFIG 0xD4

#define ADM1272__MODEL_LEN  10   /* "ADM1272-A1" */

static const char adm1272__model[7] = {'A', 'D', 'M', '1', '2', '7', '2'};

static int adm1272_read_word(unsigned addr, unsigned cmd, unsigned *raw)
{
    i2c_u8 b[2];
    int e;
    i2c_init();
    e = i2c_read(addr, (int)(cmd & 0xFF), b, 2);
    if (e) return e;
    *raw = (unsigned)b[0] | ((unsigned)b[1] << 8);
    return 0;
}

static int adm1272_identify(unsigned addr)
{
    i2c_u8 id[4];
    i2c_u8 mdl[ADM1272__MODEL_LEN + 1];
    int e, i;
    i2c_init();
    e = i2c_read(addr, ADM1272_MFR_ID, id, 4);          /* count 3 + "ADI": whole block */
    if (e) return -e;
    if (id[0] != 3 || id[1] != 'A' || id[2] != 'D' || id[3] != 'I') return 0;
    e = i2c_read(addr, ADM1272_MFR_MODEL, mdl, ADM1272__MODEL_LEN + 1);
    if (e) return -e;
    if (mdl[0] != ADM1272__MODEL_LEN) return 0;
    for (i = 0; i < 7; i++)
        if (mdl[i + 1] != (i2c_u8)adm1272__model[i]) return 0;
    return 1;
}

/* coefficients from PMON_CONFIG */
static int adm1272__coef(unsigned addr, unsigned *mv, unsigned *mi, unsigned *mp)
{
    unsigned cfg;
    int e = adm1272_read_word(addr, ADM1272_PMON_CONFIG, &cfg);
    int vr, ir;
    if (e) return e;
    vr = (cfg & 0x20) != 0;
    ir = (cfg & 0x01) != 0;
    *mv = vr ? 4062u : 6770u;
    *mi = ir ? 663u : 1326u;
    if (vr) *mp = ir ? 10535u : 21071u;
    else    *mp = ir ? 17561u : 34474u;
    return 0;
}

/* mV = Y * 100000 / m, rounded, no overflow on 32 bit */
static long adm1272__mv(unsigned y, unsigned m)
{
    unsigned long t = (unsigned long)(y & 0x0FFF) * 50000UL;
    unsigned long q = t / m, r = t % m;
    return (long)(q * 2UL + (r * 2UL + m / 2) / m);
}

static int adm1272_read_vi(unsigned addr, unsigned long rs_uohm,
                           long *vin_mv, long *vout_mv, long *iout_ma)
{
    unsigned mv, mi, mp, y;
    long num, a, q, r, t;
    int e;
    if (rs_uohm == 0) return 1;
    e = adm1272__coef(addr, &mv, &mi, &mp);
    if (e) return e;

    e = adm1272_read_word(addr, ADM1272_READ_VIN, &y);
    if (e) return e;
    *vin_mv = adm1272__mv(y, mv);

    e = adm1272_read_word(addr, ADM1272_READ_VOUT, &y);
    if (e) return e;
    *vout_mv = adm1272__mv(y, mv);

    e = adm1272_read_word(addr, ADM1272_READ_IOUT, &y);
    if (e) return e;
    /* I[mA] = (10*Y - 20480) * 1e6 / (mi * rs_uohm) */
    num = (long)(y & 0x0FFF) * 10L - 20480L;
    a = num * 1000L;
    q = a / (long)mi;
    r = a % (long)mi;
    t = q * 1000L + (r * 1000L) / (long)mi;     /* num*1e6/mi */
    *iout_ma = t / (long)rs_uohm;
    return 0;
}

static int adm1272_read_pin(unsigned addr, unsigned long rs_uohm, long *pin_mw)
{
    unsigned mv, mi, mp, y;
    unsigned long a, q, r, t, q2, r2;
    int e;
    if (rs_uohm == 0) return 1;
    e = adm1272__coef(addr, &mv, &mi, &mp);
    if (e) return e;
    e = adm1272_read_word(addr, ADM1272_READ_PIN, &y);
    if (e) return e;
    /* P[mW] = Y * 1e9 / (mp * rs_uohm) */
    a = (unsigned long)y * 1000UL;
    q = a / mp;
    r = a % mp;
    t = q * 1000UL + (r * 1000UL) / mp;          /* Y*1e6/mp */
    q2 = t / rs_uohm;
    r2 = t % rs_uohm;
    *pin_mw = (long)(q2 * 1000UL + (r2 * 1000UL + rs_uohm / 2) / rs_uohm);
    return 0;
}

#endif /* FRAG_ADM1272_H */
