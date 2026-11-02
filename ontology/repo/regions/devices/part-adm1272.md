---
id: part-adm1272
name: "adm1272: PMBus hot-swap controller / power monitor (Analog Devices)"
kind: device
one_liner: "adm1272 at 0x10: PMBus hot-swap controller / power monitor (Analog Devices) — VERIFIED against the twin's controls"
injected_by: tools/ontology.py
parent: devices
use_when: "보드 입력 전압이 몇 V인지 물을 때 · 부하에 흐르는 전류(A)를 확인할 때 · 지금 소비 전력(W)을 알고 싶을 때 · 출력 전압이 떨어졌는지 점검할 때 · 전원 레일에 붙은 PMBus 칩이 ADM1272인지 확인할 때"
---
# adm1272 — PMBus hot-swap controller / power monitor (Analog Devices)
**Status:** VERIFIED against the twin's controls.
Learned by Claude Code CLI (claude -p / agent with probe.py only) on 2026-10-13 13:06 in 2 round(s) on the lab bench.

**Default address:** 0x10 (a body's wiring may move it; the hub fills POKE_ADDR_ADM1272)

**Use when:** 보드 입력 전압이 몇 V인지 물을 때 · 부하에 흐르는 전류(A)를 확인할 때 · 지금 소비 전력(W)을 알고 싶을 때 · 출력 전압이 떨어졌는지 점검할 때 · 전원 레일에 붙은 PMBus 칩이 ADM1272인지 확인할 때

**Fragment** `adm1272.h` — functions:
- `static int adm1272_identify(unsigned addr)  /* Returns 1 if MFR_ID (0x99) is the block 'ADI' and MFR_MODEL (0x9A, read whole: count 10 + 10 bytes) starts with 'ADM1272', 0 if not, negative if the bus failed. Reads whole blocks so no bytes stay queued in the part. */`
- `static int adm1272_read_word(unsigned addr, unsigned cmd, unsigned *raw)  /* Reads the raw little-endian PMBus word at cmd (0x88 VIN, 0x8B VOUT, 0x8C IOUT, 0x97 PIN, 0xD4 PMON_CONFIG). 0 = ok, or the i2c error. */`
- `static int adm1272_read_vi(unsigned addr, unsigned long rs_uohm, long *vin_mv, long *vout_mv, long *iout_ma)  /* Reads VIN and VOUT in mV (0..100000) and IOUT in mA (signed). rs_uohm = sense resistor in µΩ (the bench twin uses 300). Ranges from PMON_CONFIG. 0 = ok, 1 if rs = 0, or the i2c error. */`
- `static int adm1272_read_pin(unsigned addr, unsigned long rs_uohm, long *pin_mw)  /* Reads input power in mW. rs_uohm = sense resistor in µΩ (the bench twin uses 300). 0 = ok, 1 if rs = 0, or the i2c error. */`

**May write:** nothing (read only) — the exact registers, value ranges and counts are the hub's safety-reviewed policy (incubated/fn.json), not this text.
**Identification evidence:** PMBus command codes answer: 0x98 PMBUS_REVISION=0x22; block 0x99 MFR_ID = 03 'ADI'; block 0x9A MFR_MODEL = 0A 'ADM1272-A1'; 0x9B MFR_REVISION = 02 '25'; 0x01 OPERATION=0x80; 0x20 VOUT_MODE=0x40 (direct); 0xD4 PMON_CONFIG=0x3F35

**Registers:** PMBus: 0x01 OPERATION, 0x20 VOUT_MODE, 0x78/0x79 STATUS (0), 0x88 READ_VIN, 0x8B READ_VOUT, 0x8C READ_IOUT, 0x8D READ_TEMP1, 0x97 READ_PIN, 0x98-0x9B PMBus revision/MFR blocks, 0xD3 PMON_CONTROL=0x0101, 0xD4 PMON_CONFIG (bit5 VRANGE=100V, bit0 IRANGE=30mV, bit3 TEMP1_EN=0). Words little-endian, direct format X=(Y*10^-R - b)/m: V m=4062 b=0 R=-2; I m=663*Rs(mOhm) b=20480 R=-1; P m=10535*Rs R=-3

**Identify:** `adm1272_identify(addr)` returns 1 at this part and not at the bench's other parts (id)
