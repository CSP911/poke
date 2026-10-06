---
id: commands
name: "Hub commands"
kind: protocol
one_liner: "The UDP commands the kernel answers: staging, injection, process control, motion, services"
injected_by: tools/ontology.py
parent: platform
---
# Hub commands
Every frame is `POKE` + len32 + payload; replies are `RESP` + len32 + data. Binaries larger than one
datagram are staged in 1024-byte `EXLD` chunks (offset + data, acked "ok N"), then a finisher names
the staged image by length and Fletcher-32 checksum.

| command | what it does |
|---|---|
| `PING`, `INFO` | liveness; JSON with build, el, mmu, ram, pool, resident, persona, touch, last_log |
| `EXLD` + `EXRN` | stage a flat aarch64 probe, run it once in kernel context (legacy, small probes) |
| `URUN` | run a staged image as an EL0 unit; payload may carry capability mappings |
| `PRST` / `PRUN` / `PSTP` | persona: stage, start (tick loop over api_t), stop |
| `RSLD` / `RSTP` | resident driver: inject with mappings + name, stop (RES_STOP via sleep-return) |
| `SREQ` | request to the resident through its service page; reply `SRSP` status + data |
| `MLOD` / `MATT` / `MSTP` / `MSTO` | motion skill: load with body (joints, limits, axes), arm an attempt with 8 params, step once (returns filtered targets + safety counters), stop |
| `KRLD` | replace the kernel over the network (trampoline at 0x00700000) |
| `GPIO`, `TEMP`, `DRAW`, `TIME` | small direct services |
