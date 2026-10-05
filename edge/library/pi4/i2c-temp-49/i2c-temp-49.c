#include "../../../kernel/pi4/poke_api.h"
#include "i2c_bus.h"
#include <stdint.h>

#define DEV_ADDR   0x49
#define REG_TEMP   0x00

static uint64_t last_seq = 0;

static uint64_t build_temp_json(char *buf, int64_t temp_mC)
{
    uint64_t len = 0;
    len += fmt_str(buf + len, "{\"temp_mC\": ");
    len += fmt_int(buf + len, temp_mC);
    len += fmt_str(buf + len, "}");
    return len;
}

static int64_t read_temp_mC(void)
{
    static uint8_t raw[2];

    raw[0] = 0;
    raw[1] = 0;

    if (i2c_read(DEV_ADDR, REG_TEMP, raw, 2) != 0) {
        return -1000000; /* sentinel error value, unlikely in practice */
    }

    int32_t word16 = ((int32_t)raw[0] << 8) | (int32_t)raw[1];
    int32_t value12 = word16 >> 4; /* 12-bit left-justified value */

    if (value12 & 0x800) {
        value12 |= ~0xFFF; /* sign extend 12-bit to 32-bit */
    }

    /* temp_C = value12 * 0.0625  => temp_mC = value12 * 62.5 = value12*125/2 */
    int64_t temp_mC = ((int64_t)value12 * 125) / 2;
    return temp_mC;
}

uint64_t __attribute__((section(".text.main"))) resident_main(const api_t *api, uint64_t op, uint64_t arg)
{
    (void)arg;

    switch (op) {
    case 4: /* RES_NAME */
        return (uint64_t)"i2c-temp-49";

    case 1: /* RES_INIT */
        i2c_init();
        return 0;

    case 2: { /* RES_TICK */
        svc_page_t *sp = (svc_page_t *)UNIT_SVC_VA;

        if (sp->req_seq != last_seq) {
            last_seq = sp->req_seq;

            if (sp->op == 1) {
                int64_t temp_mC = read_temp_mC();
                uint64_t len = build_temp_json((char *)sp->rsp, temp_mC);
                sp->rsp_len = len;
                sp->status = 0;
            } else {
                sp->rsp_len = 0;
                sp->status = 1;
            }

            __asm__ volatile("dsb sy" ::: "memory");
            sp->rsp_seq = sp->req_seq;
        }
        return 0;
    }

    case 3: /* RES_STOP */
        return 0;

    default:
        return 0;
    }
}
