---
id: i2c
name: "i2c"
kind: driver
one_liner: "Pi 4 bare-metal I2C bus service on BSC1 (GPIO 2 SDA / GPIO 3 SCL, 100 kHz, PIO)."
injected_by: tools/ontology.py
parent: drivers
---
# i2c
    Pi 4 bare-metal I2C bus service on BSC1 (GPIO 2 SDA / GPIO 3 SCL, 100 kHz, PIO). Not a device driver: the hub and the language model reach any device on the bus through SREQ ops 1 scan, 2 read(addr, reg, n), 3 write(addr, bytes) — the tool with which an unknown device gets incubated. Same address on the QEMU twin (i2c-bus.1), where virtual devices can be plugged in with -device.

    **Provides:** i2c · **inject:** `poke resident i2c` · **source:** `i2c.c`

    ## Capability windows
    - `0xFE804000` len `0x1000` device — BSC1 registers
- `0xFE200000` len `0x1000` device — GPIO: GPFSEL0 / pull control for GPIO 2-3

## Hardware notes
- **controller**: BCM2711 BSC1 at 0xFE804000 (BSC0 0xFE205000 and BSC2 0xFE805000 are the other two)
- **pins**: GPIO 2 (SDA1) / GPIO 3 (SCL1): the resident sets ALT0 + pull-up itself at INIT (GPFSEL0, GPIO_PUP_PDN_CNTRL_REG0)
- **clock**: DIV 1500 from the 150 MHz core clock = 100 kHz
- **twin**: QEMU raspi4b: i2c-bus.0/1/2 = BSC0/1/2; tmp105, lsm303dlhc_mag, ds1338, pca9552 models work; at24c-eeprom crashes QEMU when probed
