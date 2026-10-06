---
id: i2c-0x1e
name: "I2C 0x1e: HMC5883L-register-compatible 3-axis digital magnetometer (QE"
kind: recipe
one_liner: "At 0x1e: HMC5883L-register-compatible 3-axis digital magnetometer (QEMU emulates it as lsm303dlhc_mag, which mirrors th — VERIFIED: a driver written from this reads correct values on the twin"
injected_by: tools/ontology.py
parent: devices
---
# I2C 0x1e — HMC5883L-register-compatible 3-axis digital magnetometer (QEMU emulates it as lsm303dlhc_mag, which mirrors the HMC5883L register map/ID bytes)
**Status: verified.** The resident `i2c-mag-1e` was written from this identification and its readings
matched ground truth on the twin (2026-10-06 00:30).

**Evidence:** ID registers 0x0A-0x0C read 0x48,0x34,0x33 = ASCII 'H','4','3', the fixed HMC5883L signature. Config regs 0x00-0x02 = 0x10,0x20,0x03: CRA/CRB are HMC5883L power-on defaults, Mode=0x03 = idle, which explains why data regs 0x03-0x08 are all zero (no conversion run, not a fault). Could not fully separate from LSM303DLHC-mag since that part deliberately mirrors the same register map/ID bytes for compatibility; the QEMU model name (lsm303dlhc_mag) suggests that's the underlying emulation even though the bus signature reads as HMC5883L. Did not force continuous mode (register already read, but field value is host-set via qom and likely 0 regardless, so the test would not have been decisive) to avoid an inconclusive write.

**Reading:** Write Mode reg 0x02=0x00 for continuous conversion, then read 6 bytes from 0x03: order is X_MSB,X_LSB,Z_MSB,Z_LSB,Y_MSB,Y_LSB, each signed 16-bit; scale by gain setting in CRB (default 0x20 -> 1090 LSB/Gauss for X/Y, ~980 for Z).
