---
id: part-pca9554
name: "pca9554: 8-bit I2C GPIO expander (4 registers: input, output, invert,"
kind: device
one_liner: "pca9554 at 0x20: 8-bit I2C GPIO expander (4 registers: input, output, invert, direction) — VERIFIED against the twin's controls"
injected_by: tools/ontology.py
parent: devices
use_when: "read whether a button, switch or contact on an expander pin is high or low · turn a relay, LED or enable line on an expander pin on or off · check the level of all eight expander pins at once · confirm that an output pin really took the level it was set to"
---
# pca9554 — 8-bit I2C GPIO expander (4 registers: input, output, invert, direction)
**Status:** VERIFIED against the twin's controls.
Learned by Claude Code CLI (claude -p / agent with probe.py only) on 2026-10-11 22:03 in 1 round(s) on the ext bench.

**Default address:** 0x20 (a body's wiring may move it; the hub fills POKE_ADDR_PCA9554)

**Use when:** read whether a button, switch or contact on an expander pin is high or low · turn a relay, LED or enable line on an expander pin on or off · check the level of all eight expander pins at once · confirm that an output pin really took the level it was set to

**Fragment** `pca9554.h` — functions:
- `static int pca9554_read(unsigned addr, unsigned *levels)  /* true level of P0..P7 as a bit mask (bit n = Pn, 1 = high, 0..255), invert register undone; 0 = ok */`
- `static int pca9554_set_pin(unsigned addr, unsigned pin, unsigned level)  /* make pin 0..7 an output and drive it high (level != 0) or low (level 0), other pins unchanged; 1 = bad pin, 0 = ok */`
- `static int pca9554_identify(unsigned addr)  /* Reads only. Returns 1 if each of registers 0..3 gives the same byte twice in a 2-byte read, registers 4..7 read the same as 0..3, and the four are not all one value. Returns 0 if any check fails (a part that reads the same value everywhere, such as 0xFF, is not taken as this part). Returns a negative bus error if a read fails. The part has no ID register. */`

**May write:** nothing (read only) — the exact registers, value ranges and counts are the hub's safety-reviewed policy (incubated/fn.json), not this text.
**Identification evidence:** 0x04 reads the same as 0x00 and 0x07 the same as 0x03 (only low 2 bits of register number used); register 2=0x00, register 3=0xF0; writing 0x0A to register 1 made register 0 read 0xFA; writing 0xFF to register 2 made it read 0x05; setting register 3=0x00 and register 1=0x00 with inversion on read 0xFF; not a PCF8574 (writing 0x00 did not change the pins)

**Registers:** 0 input (read-only), 1 output, 2 invert (1 = inverted), 3 direction (1 = input); only the low 2 bits of the register number count, so it repeats every 4 registers; reading many bytes repeats the same register

**Identify:** `pca9554_identify(addr)` returns 1 at this part and not at the bench's other parts (id)
