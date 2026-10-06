---
id: pi4-hardware
name: "Raspberry Pi 4 (BCM2711)"
kind: device
one_liner: "The physical edge: Cortex-A72 ×4, 4 GB, GENET ethernet, EMMC2 SD, BSC I2C, PCIe → VL805 USB"
injected_by: tools/ontology.py
parent: platform
---
# Raspberry Pi 4
Boots the kernel from the SD's FAT partition (the SD kernel is older; `poke kernel` reloads over
the network). Hub link: GENET ethernet, 10.0.0.2. Windows the drivers use: EMMC2 0xFE340000,
BSC1 0xFE804000 (GPIO 2/3), GPIO 0xFE200000, PCIe host bridge 0xFD500000, xHCI window 0x600000000.
Memory layout: kernel 0–16 MB, GENET buffers 0x02000000, xHCI DMA 0x02200000, page pool from
0x40000000. The SD's second partition (14.1 GB) is the POKE store.
