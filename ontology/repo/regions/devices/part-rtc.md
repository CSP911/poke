---
id: part-rtc
name: "rtc: i2c-rtc-68"
kind: device
one_liner: "rtc at 0x68: i2c-rtc-68 — VERIFIED (an earlier incubation's library driver, re-cut as a fragment)"
injected_by: tools/ontology.py
parent: devices
use_when: "need to read the time from the hardware clock · want the current date on the device · timestamping a log entry or sensor reading · setting the hardware clock to a known date and time · found something answering at I2C address 0x68 and want to know what it is"
---
# rtc — i2c-rtc-68
**Status:** VERIFIED (an earlier incubation's library driver, re-cut as a fragment).
From the incubated driver i2c-rtc-68.

**Default address:** 0x68 (a body's wiring may move it; the hub fills POKE_ADDR_RTC)

**Use when:** need to read the time from the hardware clock · want the current date on the device · timestamping a log entry or sensor reading · setting the hardware clock to a known date and time · found something answering at I2C address 0x68 and want to know what it is

**Fragment** `rtc.h` — functions:
- `static int rtc_read(unsigned addr, int *year, int *month, int *day, int *hour, int *min, int *sec)  /* 24-hour, year as 4 digits; 0 = ok */`
- `static int rtc_set(unsigned addr, int year, int month, int day, int hour, int min, int sec)  /* 0 = ok */`

**May write:** nothing (read only) — the exact registers, value ranges and counts are the hub's safety-reviewed policy (incubated/fn.json), not this text.
