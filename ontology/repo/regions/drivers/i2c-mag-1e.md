---
id: i2c-mag-1e
name: "i2c-mag-1e"
kind: driver
one_liner: "Need raw 3-axis magnetometer readings · Reading HMC5883L-compatible sensor over I2C at address 0x1e · Querying i2c-mag-1e driver via SREQ op 1 for x/y/z counts · Working with QEMU lsm303dlhc_mag emulated compass/magnetometer · Interpreting signed 16-bit X/Y/Z magnetic field output before scaling to"
injected_by: tools/ontology.py
parent: drivers
---
# i2c-mag-1e
    HMC5883L 3-axis digital magnetometer (Honeywell/DFRobot compass module) at I2C 0x1e on BSC1 — driver written and verified by claude-sonnet-5 during AI incubation

    **Provides:** magnetometer · **inject:** `poke resident i2c-mag-1e` · **source:** `i2c-mag-1e.c`

    ## Capability windows
    - `0xFE804000` len `0x1000` device — BSC1
- `0xFE200000` len `0x1000` device — GPIO (pins to ALT0)

## Service
- op1: `{"x": <int raw>, "y": <int raw>, "z": <int raw>}  (raw signed 16-bit counts, X Y Z order as the part defines)`

## Provenance
Written by **claude-sonnet-5** during AI incubation on 2026-10-06 00:30, verified on **twin** in 1 round(s). Evidence from identification: Reading registers 0x0A-0x0C (Identification Registers A/B/C) returned bytes 0x48,0x34,0x33 = ASCII 'H','4','3', which is the exact fixed ID string documented for the HMC5883L - a signature unique to this part.

**Use when:** Need raw 3-axis magnetometer readings · Reading HMC5883L-compatible sensor over I2C at address 0x1e · Querying i2c-mag-1e driver via SREQ op 1 for x/y/z counts · Working with QEMU lsm303dlhc_mag emulated compass/magnetometer · Interpreting signed 16-bit X/Y/Z magnetic field output before scaling to
