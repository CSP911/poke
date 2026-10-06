---
id: check-memory
name: "check-memory"
kind: driver
one_liner: "Destructive RAM test of all memory the kernel is not using, run by the hub chunk by chunk with parameter-patched EXEC probes (fixed patterns + inverse pass, address pattern)."
injected_by: tools/ontology.py
parent: drivers
---
# check-memory
Destructive RAM test of all memory the kernel is not using, run by the hub chunk by chunk with parameter-patched EXEC probes (fixed patterns + inverse pass, address pattern). MMU is off, so every access hits DRAM uncached.

**Provides:**  · **inject:** `poke resident check-memory` · **source:** `memtest.c`

## Capability windows
- none
