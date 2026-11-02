---
id: part-tmp421
name: "tmp421: temperature sensor with one remote channel (board temperatur"
kind: device
one_liner: "tmp421 at 0x4c: temperature sensor with one remote channel (board temperature plus one remote sensor) — VERIFIED against the twin's controls"
injected_by: tools/ontology.py
parent: devices
use_when: "보드(칩 자체) 온도가 몇 도인지 알고 싶을 때 · 원격 다이오드에 붙은 부품(CPU, 방열판 등) 온도를 재야 할 때 · 보드 온도와 원격 온도를 함께 보고해야 할 때 · 과열 여부를 확인하려고 현재 온도를 읽을 때"
---
# tmp421 — temperature sensor with one remote channel (board temperature plus one remote sensor)
**Status:** VERIFIED against the twin's controls.
Learned by Claude Code CLI (claude -p / agent with probe.py only) on 2026-10-11 22:02 in 1 round(s) on the ext bench.

**Default address:** 0x4c (a body's wiring may move it; the hub fills POKE_ADDR_TMP421)

**Use when:** 보드(칩 자체) 온도가 몇 도인지 알고 싶을 때 · 원격 다이오드에 붙은 부품(CPU, 방열판 등) 온도를 재야 할 때 · 보드 온도와 원격 온도를 함께 보고해야 할 때 · 과열 여부를 확인하려고 현재 온도를 읽을 때

**Fragment** `tmp421.h` — functions:
- `static int tmp421_read_mc(unsigned addr, unsigned channel, long *mc)  /* channel 0 = local (board), 1 = remote; *mc = temperature in milli-°C (0.0625 °C steps, 0..127000 standard range, -64000..191000 extended range); 0 = ok, 1 = bad channel, else i2c error */`
- `static int tmp421_read_both(unsigned addr, long *local_mc, long *remote_mc)  /* board and remote temperatures in milli-°C (0.0625 °C steps); 0 = ok, else i2c error */`
- `static int tmp421_identify(unsigned addr)  /* Reads the manufacturer ID (0xFE) and device ID (0xFF). Returns 1 if they are 0x55 (TI) and 0x21 (TMP421), 0 for any other part (TMP422 and TMP423 report 0x22 and 0x23), and a negative value if the bus read fails. */`

**May write:** nothing (read only) — the exact registers, value ranges and counts are the hub's safety-reviewed policy (incubated/fn.json), not this text.
**Identification evidence:** reg 0xFE=0x55 (TI manufacturer ID), reg 0xFF=0x21 (TMP421 device ID); 2-byte read of 0x00=18 70 and 0x01=24 30 match the fraction bytes in 0x10=0x70 and 0x11=0x30; power-on values 0x08=00, 0x09=00, 0x0A=1C, 0x0B=07 match the datasheet

**Registers:** 00 local temp whole °C, 10 local fraction (bits 7:4 x 0.0625 °C), 01 remote temp whole °C, 11 remote fraction; 2-byte read returns whole byte then fraction byte, no pointer auto-increment; 08 status, 09 config1 (0 = standard range 0-127 °C, running), 0A config2 (0x1C = local and remote channels on), 0B conversion rate (07 = 8 per second), 21 reads 0x30 (N-factor correction expected per datasheet, not verified), FE manufacturer ID, FF device ID

**Identify:** `tmp421_identify(addr)` returns 1 at this part and not at the bench's other parts (id)
