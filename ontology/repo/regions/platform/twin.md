---
id: twin
name: "QEMU twin"
kind: platform-rule
one_liner: "The same kernel on a virtual Pi 4; what is identical and what differs from the hardware"
injected_by: tools/ontology.py
parent: platform
---
# QEMU twin
`kernel8-qemu.img` is the kernel built with `-DNO_ETH`: the protocol rides the mini UART (QEMU
serial 1), exposed as TCP by `pokeedge.Twin()`. EL1, MMU, EL0 units, scheduler and safety filter are
identical to the hardware. Differences that matter:
- the SD card sits on the older EMMC at 0xFE300000 (hardware: EMMC2 0xFE340000) — build residents with `-DEMMC_BASE`
- one PL011 only: no UART2–5
- I2C buses i2c-bus.0/1/2 = BSC0/1/2 at the hardware addresses; virtual devices via `-device`
- the clock is host wall time, so the motion watchdog is 200 ms instead of 20 ms
- the protocol socket accepts one connection; share the link
- monitor (`qom-set t1 temperature 23500`) sets device state for ground truth
