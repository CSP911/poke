typedef unsigned long u64;
typedef unsigned int u32;
typedef unsigned char u8;

#include "../../../kernel/pi4/poke_api.h"
#include "i2c_bus.h"

#define RTC_ADDR 0x68

static u64 last_seq = 0;

static u32 bcd2dec(u8 b)
{
    return (u32)((b >> 4) * 10 + (b & 0x0F));
}

static u64 do_init(const api_t *api)
{
    i2c_init();
    api->log("i2c-rtc-68: init ok");
    return 0;
}

static void build_time_json(void)
{
    static u8 raw[7];
    static char buf[96];
    u32 n = 0;

    /* read fresh: registers 0x00-0x06 */
    i2c_read(RTC_ADDR, 0x00, raw, 7);

    u32 sec  = bcd2dec(raw[0] & 0x7F);
    u32 minu = bcd2dec(raw[1] & 0x7F);
    u32 hour = bcd2dec(raw[2] & 0x3F);
    u32 date = bcd2dec(raw[4] & 0x3F);
    u32 mon  = bcd2dec(raw[5] & 0x1F);
    u32 year = 2000 + bcd2dec(raw[6]);

    n += fmt_str(buf + n, "{\"utc\": \"");
    n += fmt_pad(buf + n, hour, 2);
    buf[n++] = ':';
    n += fmt_pad(buf + n, minu, 2);
    buf[n++] = ':';
    n += fmt_pad(buf + n, sec, 2);
    n += fmt_str(buf + n, "\", \"date\": \"");
    n += fmt_pad(buf + n, year, 4);
    buf[n++] = '-';
    n += fmt_pad(buf + n, mon, 2);
    buf[n++] = '-';
    n += fmt_pad(buf + n, date, 2);
    n += fmt_str(buf + n, "\"}");

    svc_page_t *sp = (svc_page_t *)UNIT_SVC_VA;

    for (u32 i = 0; i < n; i++) {
        sp->rsp[i] = (u8)buf[i];
    }
    sp->rsp_len = n;
    sp->status = 0;
}

static void handle_tick(void)
{
    svc_page_t *sp = (svc_page_t *)UNIT_SVC_VA;

    if (sp->req_seq == last_seq) {
        return;
    }

    u64 op = sp->op;

    if (op == 1) {
        build_time_json();
    } else {
        sp->rsp_len = 0;
        sp->status = 1;
    }

    last_seq = sp->req_seq;

    __asm__ volatile("dsb sy" ::: "memory");
    sp->rsp_seq = sp->req_seq;
}

__attribute__((section(".text.main")))
u64 resident_main(const api_t *api, u64 op, u64 arg)
{
    (void)arg;

    switch (op) {
    case 4: /* RES_NAME */
        return (u64)"i2c-rtc-68";
    case 1: /* RES_INIT */
        return do_init(api);
    case 2: /* RES_TICK */
        handle_tick();
        return 0;
    case 3: /* RES_STOP */
        return 0;
    default:
        return 0;
    }
}
