---
id: i2c-temp-49
name: "i2c-temp-49"
kind: driver
one_liner: "Reading ambient or board temperature · Checking I2C device at address 0x49 · Querying temp_mC via SREQ op 1 · Debugging TMP102/TMP112 sensor readings · Monitoring thermal conditions over I2C"
injected_by: tools/ontology.py
parent: drivers
---
# i2c-temp-49
    TMP102/TMP112-style I2C temperature sensor (12-bit, left-justified in 16-bit word) at I2C 0x49 on BSC1 — driver written and verified by claude-sonnet-5 during AI incubation

    **Provides:** temperature · **inject:** `poke resident i2c-temp-49` · **source:** `i2c-temp-49.c`

    ## Capability windows
    - `0xFE804000` len `0x1000` device — BSC1
- `0xFE200000` len `0x1000` device — GPIO (pins to ALT0)

## Service
- op1: `{"temp_mC": <int, milli-degrees C>}`

## Provenance
Written by **claude-sonnet-5** during AI incubation on 2026-10-06 00:21, verified on **twin** in 2 round(s). Evidence from identification: Reading pointer 0x00 (temperature register) gave bytes [0x1B,0x80]=0x1B80; right-shifting 4 bits (12-bit left-justified format used by TMP102/TMP112) gives 0x1B8=440, *0.0625°C/LSB = 27.5°C, a sane room temperature. Pointer 0x01 (config register) returned a plausible low-activity config word (0x0080), consistent with a config register rather than random data.

**Use when:** Reading ambient or board temperature · Checking I2C device at address 0x49 · Querying temp_mC via SREQ op 1 · Debugging TMP102/TMP112 sensor readings · Monitoring thermal conditions over I2C
