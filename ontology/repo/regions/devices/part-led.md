---
id: part-led
name: "led: i2c-led-61"
kind: device
one_liner: "led at 0x60: i2c-led-61 — VERIFIED (an earlier incubation's library driver, re-cut as a fragment)"
injected_by: tools/ontology.py
parent: devices
use_when: "turn an LED on the PCA9552 expander on or off · make an LED blink at PWM0 or PWM1 rate · read which of the 16 expander pins are high or low right now · check a button wired to a PCA9552 pin"
---
# led — i2c-led-61
**Status:** VERIFIED (an earlier incubation's library driver, re-cut as a fragment).
From the incubated driver i2c-led-61.

**Default address:** 0x60 (a body's wiring may move it; the hub fills POKE_ADDR_LED)

**Use when:** turn an LED on the PCA9552 expander on or off · make an LED blink at PWM0 or PWM1 rate · read which of the 16 expander pins are high or low right now · check a button wired to a PCA9552 pin

**Fragment** `led.h` — functions:
- `static int led_set(unsigned addr, int led, int mode)  /* led 0..15; mode 0 = on, 1 = off, 2 = blink rate 0 (PWM0), 3 = blink rate 1 (PWM1); 0 = ok */`
- `static int led_inputs(unsigned addr, unsigned *pins)  /* 16 pin levels, bit i = pin i (an LED that is on reads 0); 0 = ok */`

**May write:** nothing (read only) — the exact registers, value ranges and counts are the hub's safety-reviewed policy (incubated/fn.json), not this text.
