---
id: drivers
name: "Library residents"
kind: topic
one_liner: "Injected drivers: what each one gives the hub, what windows it needs, and how it was verified"
injected_by: tools/ontology.py
role: representative
use_when: "which resident provides touch, block storage, an I2C bus, or a sensor reading · what windows a driver needs mapped · how to inject or stop one · what a driver's service ops are · which drivers were written by AI and where they were verified"
---
# Library residents
A resident is a flat binary (1–9 KB) injected with `RSLD` + capability mappings from its
`device.json`. It runs as an EL0 process with only those windows, talks to the hub through its
service page, and is stopped with `RSTP`. One resident slot exists today (SLOT_RESIDENT), so the SD
driver steps aside for a device driver after what is needed was read. Nothing here is linked into
the kernel.
