---
id: part-ssd0303
name: "ssd0303: monochrome OLED dot-matrix display controller, write-only (t"
kind: device
one_liner: "ssd0303 at 0x3c: monochrome OLED dot-matrix display controller, write-only (the bench display) — checked only by ?"
injected_by: tools/ontology.py
parent: devices
use_when: "show a short word or number on the little OLED screen · put a status message on the bench display · blank the OLED screen and turn it on · write a reading on the display so a person can see it"
---
# ssd0303 — monochrome OLED dot-matrix display controller, write-only (the bench display)
**Status:** checked only by ?.
Learned by Claude Code CLI (claude -p / agent with probe.py only) on 2026-10-11 22:16 in 2 round(s) on the ext bench.

**Default address:** 0x3c (a body's wiring may move it; the hub fills POKE_ADDR_SSD0303)

**Use when:** show a short word or number on the little OLED screen · put a status message on the bench display · blank the OLED screen and turn it on · write a reading on the display so a person can see it

**Fragment** `ssd0303.h` — functions:
- `static int ssd0303_on(unsigned addr)  /* blank the whole display RAM (8 pages x 132 columns), then no mirror, normal (not all-on, not inverted), start line 0, display on; screen is 96x16 px; 0 = ok */`
- `static int ssd0303_text(unsigned addr, unsigned line, unsigned x, const char *s)  /* draw s left to right in a 5x7 font, 6 px per char (16 chars fit on a line); line 0 = rows 0-7, 1 = rows 8-15; x = 0-95 screen pixel column; A-Z 0-9 space, lowercase shown as uppercase, other chars blank, chars past the right edge dropped; call ssd0303_on first; 0 = ok, 1 = bad line/x */`
- `static int ssd0303_identify(unsigned addr)  /* Always returns -1 (cannot tell): this SSD1306-type OLED controller is write-only over I2C, with no ID, revision or status register to read (every read on the bench returns 0xFF), so nothing readable can confirm the part. */`

**May write:** nothing (read only) — the exact registers, value ranges and counts are the hub's safety-reviewed policy (incubated/fn.json), not this text.
**Identification evidence:** every read returns 0xFF; charge pump plus display-on alone left the screen dark; the full init (AE D5 80 A8 3F D3 00 40 8D 14 20 02 A1 C8 DA 12 D9 F1 DB 40 A4 A6 AF) then 0x40 + 32 bytes of 0xFF lit a block at the top-left; with 0x81 0xFF added the block was slightly wider; page 1 at column 0x40 lit a half-height block at the top-left; page 7 showed nothing; 0xA5 (all pixels on) did nothing visible

**Registers:** leading byte 0x00 = commands follow, 0x40 = pixel data; 0x80 = single command; SSD1306 commands (AE/AF display off/on, 8D 14 charge pump, A8 mux, DA COM pins, 20 addressing mode, B0-B7 page, 00-0F/10-1F column, 81 contrast); pixel data is 8 vertical pixels per byte; needs the full init to show anything; panel size, column offset and how writes land on screen not worked out

**Identify:** no identify() — the part has nothing to read back that names it
