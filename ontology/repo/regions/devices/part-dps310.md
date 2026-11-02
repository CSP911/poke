---
id: part-dps310
name: "dps310: Barometric pressure + temperature sensor (Infineon DPS3xx fa"
kind: device
one_liner: "dps310 at 0x77: Barometric pressure + temperature sensor (Infineon DPS3xx family) — checked only by plausible ranges + independent reading"
injected_by: tools/ontology.py
parent: devices
use_when: "someone asks for the air pressure or a barometer reading · estimating altitude or height above sea level from pressure · checking the temperature at the board or in the room · watching for weather changes from rising or falling pressure"
---
# dps310 — Barometric pressure + temperature sensor (Infineon DPS3xx family)
**Status:** checked only by plausible ranges + independent reading.
Learned by Claude Code CLI (claude -p / agent with probe.py only) on 2026-10-11 22:29 in 1 round(s) on the ext bench.

**Default address:** 0x77 (a body's wiring may move it; the hub fills POKE_ADDR_DPS310)

**Use when:** someone asks for the air pressure or a barometer reading · estimating altitude or height above sea level from pressure · checking the temperature at the board or in the room · watching for weather changes from rising or falling pressure

**Fragment** `dps310.h` — functions:
- `static int dps310_read(unsigned addr, long *temp_mc, long *press_pa)  /* compensated temperature in milli-degC (-40000..85000) and pressure in Pa (30000..120000); triggers a 1x measurement if none is ready; 0 = ok */`
- `static int dps310_read_raw(unsigned addr, long *temp_raw, long *press_raw)  /* raw 24-bit signed TMP (0x03..05) and PSR (0x00..02) at 1x oversampling, scale 524288; measures first if needed; 0 = ok */`
- `static int dps310_compensate(unsigned addr, long temp_raw, long press_raw, long *temp_mc, long *press_pa)  /* reads the chip's coefficients 0x10..0x21 and turns 24-bit raw values into milli-degC and Pa; 0 = ok */`
- `static int dps310_identify(unsigned addr)  /* 1 if product ID 0x0D reads 0x10 (DPS310/DPS368 share it); 0 if 0x0D has another non-0xFF value, 0xD0 has a Bosch-style ID (not 0xFF) or reserved MEAS_CFG bit 3 is set; -1 if a read fails or 0x0D reads 0xFF and nothing rules it out (the bench part reads 0xFF, so it gives -1 here) */`

**May write:** nothing (read only) — the exact registers, value ranges and counts are the hub's safety-reviewed policy (incubated/fn.json), not this text.
**Identification evidence:** 0xD0 reads 0xFF and 0x80-0xFF all read 0xFF, ruling out BMP280/BME280/BMP180. Map 0x00-0x28 matches DPS310 exactly: 24-bit PSR_B2..B0 = fe 2f ee, TMP_B2..B0 = 02 69 a6, PRS_CFG=00, TMP_CFG=80 (external temp sensor), MEAS_CFG=F0 (COEF_RDY|SENSOR_RDY|TMP_RDY|PRS_RDY), CFG_REG=00, 18-byte coefficient block at 0x10-0x21, COEF_SRCE 0x28=0x90. Auto-increment wraps from 0xFF to 0x00. Product ID 0x0D reads 0xFF (not 0x10), so DPS310 vs DPS368 is unconfirmed. Soft reset (w 0C 09) acks and status stays F0.

**Registers:** 0x00-02 PSR 24-bit signed raw; 0x03-05 TMP 24-bit signed raw; 0x06 PRS_CFG (rate/oversampling); 0x07 TMP_CFG (bit7 external sensor, rate/oversampling); 0x08 MEAS_CFG (status bits 7-4, mode bits 2-0); 0x09 CFG_REG; 0x0C RESET (write 0x09 = soft reset); 0x0D product ID (reads 0xFF here); 0x10-0x21 coefficients c0(12) c1(12) c00(20) c10(20) c01 c11 c20 c21 c30 (16-bit signed) = 225, -291, 81061, -57006, -1594, 1233, -9401, 91, -1222; 0x28 COEF_SRCE=0x90. Auto-increment on reads. Compensation at 1x oversampling: kT=kP=524288; T=c0/2+c1*Tsc; P=c00+Psc*(c10+Psc*(c20+Psc*c30))+Tsc*c01+Tsc*Psc*(c11+Psc*c21).

**Identify:** no identify() — the part has nothing to read back that names it

**Still to do on a real chip:** 실칩에서는 차가운 상태 시험(설정 레지스터를 지운 뒤 기록된 시험 op) 후 설정 쓰기(0x06–0x08) 재심사 필요 — 지금 권한으로는 전원 직후 설정이 필요한 실제 칩의 첫 읽기가 kind=i2c로 거절됨(안전한 실패)
