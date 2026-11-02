---
id: part-tmp105
name: "tmp105: i2c-temp-4a"
kind: device
one_liner: "tmp105 at 0x48: i2c-temp-4a — VERIFIED (an earlier incubation's library driver, re-cut as a fragment)"
injected_by: tools/ontology.py
parent: devices
use_when: "reading the room temperature from a TMP105/TMP75 on I2C · checking whether the sensor at 0x4a (or 0x48-0x4f) answers · getting temperature in milli-degrees C for a threshold or overheating check · converting the 12-bit temperature register into mC"
---
# tmp105 — i2c-temp-4a
**Status:** VERIFIED (an earlier incubation's library driver, re-cut as a fragment).
From the incubated driver i2c-temp-4a.

**Default address:** 0x48 (a body's wiring may move it; the hub fills POKE_ADDR_TMP105)

**Use when:** reading the room temperature from a TMP105/TMP75 on I2C · checking whether the sensor at 0x4a (or 0x48-0x4f) answers · getting temperature in milli-degrees C for a threshold or overheating check · converting the 12-bit temperature register into mC

**Fragment** `tmp105.h` — functions:
- `static int tmp105_read_mC(unsigned addr, long *mC)  /* temperature in milli-degrees C; 0 = ok, else the i2c error code */`

**May write:** nothing (read only) — the exact registers, value ranges and counts are the hub's safety-reviewed policy (incubated/fn.json), not this text.
