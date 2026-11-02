---
id: part-emc1403
name: "emc1403: SMBus temperature sensor, internal + 2 remote diodes (SMSC/M"
kind: device
one_liner: "emc1403 at 0x18: SMBus temperature sensor, internal + 2 remote diodes (SMSC/Microchip) — VERIFIED against the twin's controls"
injected_by: tools/ontology.py
parent: devices
use_when: "보드나 칩 내부 온도를 알고 싶을 때 · 원격 다이오드에 연결된 CPU/전원부 온도를 읽을 때 · 세 지점 온도를 한 번에 비교할 때 · 과열 여부를 확인할 때"
---
# emc1403 — SMBus temperature sensor, internal + 2 remote diodes (SMSC/Microchip)
**Status:** VERIFIED against the twin's controls.
Learned by Claude Code CLI (claude -p / agent with probe.py only) on 2026-10-13 13:11 in 2 round(s) on the lab bench.

**Default address:** 0x18 (a body's wiring may move it; the hub fills POKE_ADDR_EMC1403)

**Use when:** 보드나 칩 내부 온도를 알고 싶을 때 · 원격 다이오드에 연결된 CPU/전원부 온도를 읽을 때 · 세 지점 온도를 한 번에 비교할 때 · 과열 여부를 확인할 때

**Fragment** `emc1403.h` — functions:
- `static int emc1403_identify(unsigned addr)  /* 1 = EMC1403 (MID 0xFE=0x5D, PID 0xFD=0x21), 0 = other part, negative = bus error / cannot tell */`
- `static int emc1403_read(unsigned addr, unsigned ch, long *mc)  /* temperature of channel ch (0 internal, 1 remote 1, 2 remote 2) in m°C, 0.125 °C steps, 0..127875 (extended range -64000..191875); 0 = ok, 1 = bad channel, else i2c error */`
- `static int emc1403_read_all(unsigned addr, long *t0, long *t1, long *t2)  /* all three temperatures in m°C (internal, remote 1, remote 2); 0 = ok, else i2c error */`

**May write:** nothing (read only) — the exact registers, value ranges and counts are the hub's safety-reviewed policy (incubated/fn.json), not this text.
**Identification evidence:** 0xFE manufacturer ID = 0x5D (SMSC), 0xFD product ID = 0x21 (EMC1403), 0xFF revision = 0x04; 0x05/0x07/0x15 reset 0x55 (85 C high limits), 0x05 writable and read back; temperature registers read-only. Against: conv-rate, THERM, hysteresis, alert defaults read 0x00 and config 0x03 not writable

**Registers:** 0x00 internal temp, 0x01/0x10 ext1 temp high/low, 0x23/0x24 ext2 temp, 0x02 status, 0x03 config, 0x05 int high limit, 0x07 ext1 high limit, 0x15 ext2 high limit (reset 0x55 = 85 C), 0x0F one-shot, 0xFD PID, 0xFE MID, 0xFF REV; byte registers, no auto-increment

**Identify:** `emc1403_identify(addr)` returns 1 at this part and not at the bench's other parts (id)
