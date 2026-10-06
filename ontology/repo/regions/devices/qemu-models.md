---
id: qemu-models
name: "QEMU device model quirks"
kind: lesson
one_liner: "How QEMU's I2C, SD and sensor models differ from silicon, and which ones crash"
injected_by: tools/ontology.py
parent: devices
---
# QEMU device model quirks
- BSC (bcm2835_i2c): begins a transfer on *any* C write with I2CEN set → write C once per transfer, after A and DLEN; write data after TA; NAK = ERR without DONE; zero-length transfers never finish (scan with 1-byte reads)
- SD: the card is on the older EMMC at 0xFE300000; CSD v1 reports 64 MB for the test image; identification needs 0 polls
- tmp105: default 9-bit resolution (0.5 °C steps); `qom-set <id> temperature <milli-°C>`
- lsm303dlhc_mag: `mag-x/y/z` in field units where 100000 reads 1100 counts (1 G); refuses negative values; Z scaled by 980/1100
- ds1338: follows the host clock in UTC
- at24c-eeprom: probing it crashes QEMU — do not attach it
- pca9552: input registers read 0xFF, LS registers 0x55; still misidentified by the model (open)
