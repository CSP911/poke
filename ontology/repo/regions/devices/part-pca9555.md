---
id: part-pca9555
name: "pca9555: 16-bit I2C GPIO expander"
kind: device
one_liner: "pca9555 at 0x24: 16-bit I2C GPIO expander — VERIFIED against the twin's controls"
injected_by: tools/ontology.py
parent: devices
use_when: "read which of the 16 pins (buttons, switches, LED lines) are high or low right now · turn one LED or output pin on the expander on or off · check a single input line, e.g. whether a door contact is closed · find out whether the chip at an address really is a PCA9555/TCA9555 port expander"
---
# pca9555 — 16-bit I2C GPIO expander
**Status:** VERIFIED against the twin's controls.
Learned by Claude Code CLI (claude -p / agent with probe.py only) on 2026-10-13 13:10 in 2 round(s) on the lab bench.

**Default address:** 0x24 (a body's wiring may move it; the hub fills POKE_ADDR_PCA9555)

**Use when:** read which of the 16 pins (buttons, switches, LED lines) are high or low right now · turn one LED or output pin on the expander on or off · check a single input line, e.g. whether a door contact is closed · find out whether the chip at an address really is a PCA9555/TCA9555 port expander

**Fragment** `pca9555.h` — functions:
- `static int pca9555_read(unsigned addr, unsigned *pins)  /* reads all 16 pin levels into *pins: bit i = P0.i for i 0-7, bit 8+i = P1.i; clears polarity inversion first so 1 = high; 0 = ok */`
- `static int pca9555_set_pin(unsigned addr, unsigned pin, unsigned level)  /* makes pin 0-15 (0-7 = P0.0-P0.7, 8-15 = P1.0-P1.7) an output and drives it low (level 0) or high (level != 0); leaves the other pins as they were; 0 = ok, 1 = pin > 15 */`
- `static int pca9555_identify(unsigned addr)  /* read only; there is no ID register, so it checks the power-on map (output 2/3 = FF, polarity 4/5 = 00, config 6/7 = FF): 1 = PCA9555, 0 = not it or no ACK, -1 = polarity is 00 but outputs/config have been changed since power-on, so it cannot tell */`

**May write:** nothing (read only) — the exact registers, value ranges and counts are the hub's safety-reviewed policy (incubated/fn.json), not this text.
**Identification evidence:** regs 0-3 read FF, 4-5 read 00, 6-7 read FF, >=8 absent (PCA9555 reset map); config6=0x0F + output2=0xA5 gives input0=0xAF; polarity4=0xFF inverts input0 to 0x00; port1 outputs 0x3C read back 0x3C; plain write not reflected on read (not PCF8574)

**Registers:** 0/1 input P0/P1, 2/3 output (reset FF), 4/5 polarity inversion (reset 00), 6/7 config 1=input (reset FF); 8-bit registers

**Identify:** `pca9555_identify(addr)` returns 1 at this part and not at the bench's other parts (id)
