---
id: sdcard
name: "sdcard"
kind: driver
one_liner: "Pi 4 bare-metal SD card: BCM2711 EMMC2 (SDHCI v3) brought up by hand — reset, 400 kHz identification (CMD0/CMD8/ACMD41/CMD2/CMD3), CSD capacity, select, 25 MHz 4-bit transfer."
injected_by: tools/ontology.py
parent: drivers
---
# sdcard
    Pi 4 bare-metal SD card: BCM2711 EMMC2 (SDHCI v3) brought up by hand — reset, 400 kHz identification (CMD0/CMD8/ACMD41/CMD2/CMD3), CSD capacity, select, 25 MHz 4-bit transfer. PIO single-block read/write. Served to the hub through the resident service page (SREQ ops 1 info, 2 read, 3 write). Gives the device a memory that survives power-off: the skill library and the edge store live here.

    **Provides:** block · **inject:** `poke resident sdcard` · **source:** `sdcard.c`

    ## Capability windows
    - `0xFE340000` len `0x1000` device — EMMC2 SDHCI registers

## Hardware notes
- **controller**: BCM2711 EMMC2 at 0xFE340000 (Arasan SDHCI v3; the slot is routed here by the firmware)
- **base_clock**: mailbox clock id 12 (EMMC2), 100 MHz on Pi 4
- **mode**: 3.3 V, default speed 25 MHz, 4-bit, no UHS/1.8 V switching, PIO
- **twin**: QEMU raspi4b attaches the emulated card to the older EMMC at 0xFE300000 (STATUS bit16 'card inserted' is 0 on EMMC2 there) — build with -DEMMC_BASE=0xFE300000UL and map that window instead
