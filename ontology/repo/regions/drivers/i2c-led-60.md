---
id: i2c-led-60
name: "i2c-led-60"
kind: driver
one_liner: "Reading PCA9552 LED/GPIO state via SREQ op 1 · Decoding the input/ls register payload format for i2c-led-60 · Writing a PCA9552 LS selector register via SREQ op 2 · Confirming device at 0x60 is PCA9552, not TEA5767 · Troubleshooting LED dimmer/GPIO expander control over I2C"
injected_by: tools/ontology.py
parent: drivers
---
# i2c-led-60
    PCA9552 8-bit I2C-bus LED dimmer / GPIO expander (NOT a TEA5767 FM tuner) at I2C 0x60 on BSC1 — driver written and verified by claude-sonnet-5 during AI incubation

    **Provides:** led · **inject:** `poke resident i2c-led-60` · **source:** `i2c-led-60.c`

    ## Capability windows
    - `0xFE804000` len `0x1000` device — BSC1
- `0xFE200000` len `0x1000` device — GPIO (pins to ALT0)

## Service
- op1: `{"input": [<int reg INPUT0>, <int reg INPUT1>], "ls": [<int LS0>, <int LS1>, <int LS2>, <int LS3>]}  — and op 2 must exist: req u8 ls_index (0..3), u8 value → writes that LED-selector register (status 0 ok)`

## Provenance
Written by **claude-sonnet-5** during AI incubation on 2026-10-07 01:16, verified on **twin** in 2 round(s). Evidence from identification: Decisive test: reading INPUT0 (reg 0x00) for 4 bytes without the auto-increment bit set returned 0xFF,0xFF,0xFF,0xFF (same register sampled repeatedly, as PCA9552 only auto-increments when bit4 of the command byte is set) - a real TEA5767 would instead return its distinct 5-byte status/tuning frame, not an identical repeated byte. Then reading from command byte 0x15 (AI bit 0x10 | register 0x05=LS0) for 4 bytes returned 0x80,0x55,0x55,0x55: LS0=0x80 then LS1..LS3 auto-incremented to 0x55, exactly matching the LS-register pattern documented for this part. This supersedes the earlier 50%-confidence TEA5767 guess, which only checked a pointer-less sequential read and never tried the register/auto-increment addressing that settles it.

**Use when:** Reading PCA9552 LED/GPIO state via SREQ op 1 · Decoding the input/ls register payload format for i2c-led-60 · Writing a PCA9552 LS selector register via SREQ op 2 · Confirming device at 0x60 is PCA9552, not TEA5767 · Troubleshooting LED dimmer/GPIO expander control over I2C
