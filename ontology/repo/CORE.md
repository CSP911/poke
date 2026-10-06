# Core

POKE (Protocol for Open Kernel Execution): a hub turns a sentence into a small binary and injects it
into an OS-less device, where a 37 KB kernel runs it as an isolated process under safety rules.
Software's unit is the sentence; the binary is its pronunciation for this body and this moment, and
is thrown away. What is kept is knowledge — and this ontology is that knowledge, cut by **what a hub
needs when a sentence arrives**: the platform's contract, the drivers it can inject, what is known
about chips, and the movement skills that worked.

## What hangs on what

**A sentence becomes a binary only through the platform's contract.** Whatever is generated must be
flat aarch64, speak the syscalls, and fit the windows it is given — PLATFORM says what those are.

**Drivers are injected, never built in.** A hub that needs touch, storage or a bus looks in DRIVERS
for a resident and its `device.json` mappings; the kernel maps exactly those windows.

**Unknown hardware is incubated, and what was learned goes to DEVICES.** Identification recipes and
QEMU quirks live there so the next incubation reads before it probes. A verified driver that came out
of an incubation is listed in DRIVERS with its provenance.

**Skills are verified on a body and remembered.** SKILLS holds the ABI, the safety rules the kernel
enforces regardless of what a skill asks, and the skills a person approved with their tuned numbers.

## Areas

| Area | What it holds |
|---|---|
| `PLATFORM` | What the kernel offers to injected code, the twin, and the rules that keep it small |
| `DRIVERS` | Injected drivers, their windows, service ops and provenance |
| `DEVICES` | Chip knowledge: how to recognise and read particular parts, QEMU quirks |
| `SKILLS` | Generated movement programs, their ABI, safety and coaching |

Generated 2026-10-07 by tools/ontology.py from edge/library, docs/design, sim/body.
