typedef unsigned long u64; typedef unsigned int u32; typedef unsigned char u8;

#include "../../../kernel/pi4/poke_api.h"
#include "i2c_bus.h"

#define I2C_ADDR 0x1e

/* forward declarations so resident_main can stay first */
static int  hmc_write_reg(u8 reg, u8 val);
static int  to_signed16(u8 hi, u8 lo);
static u32  build_json(int x, int y, int z);
static void handle_svc(const api_t *api);

static u64 last_seq = 0;

static char rsp_buf[96];

static const char NAME[] = "i2c-mag-1e";

u64 resident_main(const api_t *api, u64 op, u64 arg) __attribute__((section(".text.main")));
u64 resident_main(const api_t *api, u64 op, u64 arg)
{
    (void)arg;

    switch (op) {
    case RES_NAME:
        return (u64)NAME;

    case RES_INIT:
        i2c_init();
        /* CRA: 8-sample average, 15Hz output, normal measurement */
        if (hmc_write_reg(0x00, 0x70) != 0)
            return 1;
        /* CRB: gain = 1090 LSB/Gauss (default range) */
        if (hmc_write_reg(0x01, 0x20) != 0)
            return 1;
        /* Mode: continuous measurement mode */
        if (hmc_write_reg(0x02, 0x00) != 0)
            return 1;
        api->log("hmc5883l init ok");
        return 0;

    case RES_TICK:
        handle_svc(api);
        return 0;

    case RES_STOP:
        return 0;

    default:
        return 0;
    }
}

static int hmc_write_reg(u8 reg, u8 val)
{
    u8 buf[2];
    buf[0] = reg;
    buf[1] = val;
    return i2c_write(I2C_ADDR, buf, 2);
}

static int to_signed16(u8 hi, u8 lo)
{
    u32 v = ((u32)hi << 8) | (u32)lo;
    if (v & 0x8000u)
        v |= 0xFFFF0000u;
    return (int)v;
}

static u32 build_json(int x, int y, int z)
{
    u32 p = 0;
    p += fmt_str(rsp_buf + p, "{\"x\": ");
    p += fmt_int(rsp_buf + p, x);
    p += fmt_str(rsp_buf + p, ", \"y\": ");
    p += fmt_int(rsp_buf + p, y);
    p += fmt_str(rsp_buf + p, ", \"z\": ");
    p += fmt_int(rsp_buf + p, z);
    p += fmt_str(rsp_buf + p, "}");
    return p;
}

static void handle_svc(const api_t *api)
{
    svc_page_t *sp = (svc_page_t *)UNIT_SVC_VA;

    if (sp->req_seq == last_seq)
        return;

    u64 seq = sp->req_seq;

    if (sp->op == 1) {
        u8 buf[6];
        int ok = i2c_read(I2C_ADDR, 0x03, buf, 6);

        if (ok != 0) {
            sp->status = 1;
            sp->rsp_len = 0;
            api->log("hmc5883l read failed");
        } else {
            /* HMC5883L data register order: X, Z, Y */
            int x = to_signed16(buf[0], buf[1]);
            int z = to_signed16(buf[2], buf[3]);
            int y = to_signed16(buf[4], buf[5]);

            u32 len = build_json(x, y, z);
            u32 i;
            for (i = 0; i < len; i++)
                sp->rsp[i] = rsp_buf[i];

            sp->rsp_len = len;
            sp->status = 0;
        }
    } else {
        sp->status = 1;
        sp->rsp_len = 0;
    }

    __asm__ volatile("dsb sy" ::: "memory");
    sp->rsp_seq = seq;
    last_seq = seq;
}
