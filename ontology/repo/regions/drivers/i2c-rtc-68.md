---
id: i2c-rtc-68
name: "i2c-rtc-68"
kind: driver
one_liner: "Need current time/date from RTC · Reading DS1307/DS1338 clock over I2C · Querying SREQ op 1 for utc/date fields · Verifying I2C 0x68 RTC device is responding · Debugging time sync issues with onboard hardware clock"
injected_by: tools/ontology.py
parent: drivers
---
# i2c-rtc-68
    DS1307-compatible I2C real-time clock at I2C 0x68 on BSC1 — driver written and verified by claude-sonnet-5 during AI incubation

    **Provides:** rtc · **inject:** `poke resident i2c-rtc-68` · **source:** `i2c-rtc-68.c`

    ## Capability windows
    - `0xFE804000` len `0x1000` device — BSC1
- `0xFE200000` len `0x1000` device — GPIO (pins to ALT0)

## Service
- op1: `{"utc": "HH:MM:SS", "date": "YYYY-MM-DD"}`

## Provenance
Written by **claude-sonnet-5** during AI incubation on 2026-10-06 00:26, verified on **twin** in 1 round(s). Evidence from identification: Registers 0x00-0x06 decoded as valid BCD time/calendar: sec=0x29(29), min=0x59(59), hour=0x14(14, 24h mode), day-of-week=2, date=0x05(5), month=0x10(10), year=0x26(26) -> 14:59:29 on 2026-10-05. Register 0x07 (control) = 0x00 (square-wave/out disabled). Registers 0x0E-0x12 (which on a DS3231 would be alarm/control/aging/temperature) all read 0x00, matching DS1307's plain battery-backed RAM (unused/zeroed) rather than DS3231's active temperature/alarm registers which would show non-zero, actively-updated values. WHO_AM_I-style probe at 0x75 (MPU6050 address) returned 0x00, ruling out MPU6050 at this address.

**Use when:** Need current time/date from RTC · Reading DS1307/DS1338 clock over I2C · Querying SREQ op 1 for utc/date fields · Verifying I2C 0x68 RTC device is responding · Debugging time sync issues with onboard hardware clock
