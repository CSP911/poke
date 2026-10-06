---
id: platform
name: "POKE platform"
kind: topic
one_liner: "What the kernel offers to injected code and what it refuses to become"
injected_by: tools/ontology.py
role: representative
use_when: "which command injects what (EXLD/EXRN, PRST, RSLD, URUN, MLOD, SREQ) · what a unit may call (syscalls) · how a resident talks to the hub (service page) · whether something belongs in the kernel or in a resident · the QEMU twin and how it differs from the Pi · why the kernel must stay under 64 KB"
---
# POKE platform
The kernel is 37 KB and does four things: inject binaries, isolate them as EL0 processes, keep them
within safety limits, and share time between them. Everything that knows what a device *is* lives
outside it, in the library (DRIVERS) and is injected at run time. This area holds the contract a
hub needs to generate code for the device, and the rules that keep the kernel from growing into an
operating system.
