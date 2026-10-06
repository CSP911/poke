typedef unsigned long u64;
typedef unsigned int u32;
typedef unsigned char u8;

#include "../../../kernel/pi4/poke_api.h"
#include "i2c_bus.h"

#define I2C_ADDR 0x60

/* Correct PCA9552 register map:
 * 0x00 INPUT0
 * 0x01 INPUT1
 * 0x02 PSC0
 * 0x03 PWM0
 * 0x04 PSC1
 * 0x05 PWM1
 * 0x06 LS0
 * 0x07 LS1
 * 0x08 LS2
 * 0x09 LS3
 */
#define REG_INPUT0 0x00
#define REG_LS0    0x06

static u64 last_seq = 0;

static u8 g_input[2];
static u8 g_ls[4];
static char g_buf[128];

__attribute__((section(".text.main")))
u64 resident_main(const api_t *api, u64 op, u64 arg)
{
    if (op == 4) {
        /* RES_NAME */
        return (u64)"i2c-led-60";
    }

    if (op == 1) {
        /* RES_INIT */
        i2c_init();
        return 0;
    }

    if (op == 2) {
        /* RES_TICK */
        svc_page_t *sp = (svc_page_t *)UNIT_SVC_VA;

        if (sp->req_seq != last_seq) {
            u64 seq = sp->req_seq;
            int i;

            if (sp->op == 1) {
                int pos = 0;
                const char *p1 = "{\"input\": [";
                const char *p2 = "], \"ls\": [";

                /* Fresh read of INPUT0/INPUT1 with auto-increment */
                i2c_read(I2C_ADDR, 0x10 | REG_INPUT0, g_input, 2);
                /* Fresh read of LS0..LS3 with auto-increment */
                i2c_read(I2C_ADDR, 0x10 | REG_LS0, g_ls, 4);

                while (*p1) {
                    g_buf[pos++] = *p1;
                    p1++;
                }

                pos += fmt_int(g_buf + pos, (int)g_input[0]);
                g_buf[pos++] = ',';
                g_buf[pos++] = ' ';
                pos += fmt_int(g_buf + pos, (int)g_input[1]);

                while (*p2) {
                    g_buf[pos++] = *p2;
                    p2++;
                }

                for (i = 0; i < 4; i++) {
                    pos += fmt_int(g_buf + pos, (int)g_ls[i]);
                    if (i < 3) {
                        g_buf[pos++] = ',';
                        g_buf[pos++] = ' ';
                    }
                }

                g_buf[pos++] = ']';
                g_buf[pos++] = '}';

                for (i = 0; i < pos; i++) {
                    sp->rsp[i] = (u8)g_buf[i];
                }

                sp->rsp_len = (u64)pos;
                sp->status = 0;
            } else if (sp->op == 2) {
                u8 ls_index = sp->req[0];
                u8 value = sp->req[1];

                if (ls_index < 4) {
                    u8 data[2];
                    data[0] = (u8)(REG_LS0 + ls_index);
                    data[1] = value;
                    i2c_write(I2C_ADDR, data, 2);
                    sp->rsp_len = 0;
                    sp->status = 0;
                } else {
                    sp->rsp_len = 0;
                    sp->status = 1;
                }
            } else {
                sp->rsp_len = 0;
                sp->status = 1;
            }

            __asm__ volatile("dsb sy" ::: "memory");
            sp->rsp_seq = seq;
            last_seq = seq;
        }

        return 0;
    }

    if (op == 3) {
        /* RES_STOP */
        return 0;
    }

    return 0;
}
