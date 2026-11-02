// FRAG {"name": "rtc", "use_when": "need to read the time from the hardware clock · want the current date on the device · timestamping a log entry or sensor reading · setting the hardware clock to a known date and time · found something answering at I2C address 0x68 and want to know what it is", "functions": ["static int rtc_read(unsigned addr, int *year, int *month, int *day, int *hour, int *min, int *sec)", "static int rtc_set(unsigned addr, int year, int month, int day, int hour, int min, int sec)"]}
#ifndef FRAG_RTC_H
#define FRAG_RTC_H

#include "i2c_bus.h"

/* DS1307/DS1338-compatible RTC (usually at 0x68).
 * Registers 0x00..0x06, BCD:
 *   0x00 seconds (bit7 = CH, oscillator halt)
 *   0x01 minutes
 *   0x02 hours   (bit6 = 12h mode; in 12h mode bit5 = PM, bits4..0 = 1-12;
 *                 in 24h mode bits5..0 = 0-23)
 *   0x03 day of week (1-7)
 *   0x04 date (1-31)
 *   0x05 month (1-12)
 *   0x06 year (00-99, 2000-based)
 */

static int rtc_bcd2bin(i2c_u8 v)
{
    return (int)((v >> 4) & 0x0F) * 10 + (int)(v & 0x0F);
}

static i2c_u8 rtc_bin2bcd(int v)
{
    return (i2c_u8)(((v / 10) << 4) | (v % 10));
}

/* Day of week 1-7 (1 = Sunday), Sakamoto's method. */
static int rtc_dow(int y, int m, int d)
{
    static const int t[12] = { 0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4 };
    if (m < 3) y -= 1;
    return ((y + y / 4 - y / 100 + y / 400 + t[m - 1] + d) % 7) + 1;
}

/* 24-hour, year as 4 digits; 0 = ok, bus error code (2/3/4) on bus failure,
 * -2 if the registers hold an invalid time. */
static int rtc_read(unsigned addr, int *year, int *month, int *day,
                    int *hour, int *min, int *sec)
{
    i2c_u8 regs[7];
    int i, r;
    int s, mi, h, d, mo, y;
    i2c_u8 hr;

    for (i = 0; i < 7; i++) regs[i] = 0;
    i2c_init();
    r = (int)i2c_read(addr, 0x00, regs, 7);
    if (r != 0) return r;

    s  = rtc_bcd2bin(regs[0] & 0x7F);
    mi = rtc_bcd2bin(regs[1] & 0x7F);
    hr = regs[2];
    if (hr & 0x40) {
        /* 12h mode: bit5 = PM, bits4..0 = hour 1-12 */
        h = rtc_bcd2bin(hr & 0x1F);
        if (h == 12) h = 0;
        if (hr & 0x20) h += 12;
    } else {
        h = rtc_bcd2bin(hr & 0x3F);
    }
    d  = rtc_bcd2bin(regs[4] & 0x3F);
    mo = rtc_bcd2bin(regs[5] & 0x1F);
    y  = 2000 + rtc_bcd2bin(regs[6]);

    if (s > 59 || mi > 59 || h > 23 || d < 1 || d > 31 || mo < 1 || mo > 12)
        return -2;

    if (year)  *year  = y;
    if (month) *month = mo;
    if (day)   *day   = d;
    if (hour)  *hour  = h;
    if (min)   *min   = mi;
    if (sec)   *sec   = s;
    return 0;
}

/* Sets the clock in 24h mode with CH cleared (oscillator running).
 * year 2000-2099. 0 = ok, -1 = argument out of range, else bus error code. */
static int rtc_set(unsigned addr, int year, int month, int day,
                   int hour, int min, int sec)
{
    i2c_u8 b[8];

    if (year < 2000 || year > 2099 || month < 1 || month > 12 ||
        day < 1 || day > 31 || hour < 0 || hour > 23 ||
        min < 0 || min > 59 || sec < 0 || sec > 59)
        return -1;

    b[0] = 0x00;                                    /* register pointer */
    b[1] = (i2c_u8)(rtc_bin2bcd(sec) & 0x7F);       /* CH = 0 */
    b[2] = (i2c_u8)(rtc_bin2bcd(min) & 0x7F);
    b[3] = (i2c_u8)(rtc_bin2bcd(hour) & 0x3F);      /* bit6 = 0: 24h mode */
    b[4] = (i2c_u8)rtc_dow(year, month, day);
    b[5] = (i2c_u8)(rtc_bin2bcd(day) & 0x3F);
    b[6] = (i2c_u8)(rtc_bin2bcd(month) & 0x1F);
    b[7] = rtc_bin2bcd(year - 2000);

    i2c_init();
    return (int)i2c_write(addr, b, 8);
}

#endif /* FRAG_RTC_H */
