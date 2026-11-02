---
id: part-mag
name: "mag: i2c-mag-1e"
kind: device
one_liner: "mag at 0x1e: i2c-mag-1e — VERIFIED (an earlier incubation's library driver, re-cut as a fragment)"
injected_by: tools/ontology.py
parent: devices
use_when: "reading the magnetometer's raw X/Y/Z counts · getting a compass heading or checking which way the device faces · sensing a nearby magnet or a change in the magnetic field · driving an HMC5883L-compatible or LSM303DLHC magnetometer at I2C 0x1e"
---
# mag — i2c-mag-1e
**Status:** VERIFIED (an earlier incubation's library driver, re-cut as a fragment).
From the incubated driver i2c-mag-1e.

**Default address:** 0x1e (a body's wiring may move it; the hub fills POKE_ADDR_MAG)

**Use when:** reading the magnetometer's raw X/Y/Z counts · getting a compass heading or checking which way the device faces · sensing a nearby magnet or a change in the magnetic field · driving an HMC5883L-compatible or LSM303DLHC magnetometer at I2C 0x1e

**Fragment** `mag.h` — functions:
- `static int mag_read(unsigned addr, int *x, int *y, int *z)  /* raw signed counts; puts the part into measurement mode if needed; 0 = ok */`

**May write:** nothing (read only) — the exact registers, value ranges and counts are the hub's safety-reviewed policy (incubated/fn.json), not this text.
