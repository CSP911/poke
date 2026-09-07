# Contributing to POKE

Thanks for your interest! POKE is early and moving fast — small, focused
contributions land easiest.

## Ground rules

- **Philosophy first.** POKE is bare-metal: no OS, no drivers, volatile
  binaries. If a change can be done "the easy way" (Linux, a daemon, a
  permanent binary) or "the POKE way" (bare metal, generated code,
  discard after use), we take the POKE way.
- Match the style of the file you are editing. Kernels are freestanding C
  (`-mstrict-align`, no libc); the hub is plain Node.js (no framework).

## Easiest ways to start

1. **Device library entries** (`edge/library/`) — register a chip or
   peripheral: registers, init sequence, capabilities. Architecture-neutral
   JSON; no C required. Use the Device Profile issue template.
2. **Personas** (`edge/kernel/pi4/personas/`) — small freestanding C
   applets against `poke_api.h`. Build with
   `make -C edge/kernel/pi4 personas`, keep binaries under 1400 bytes.
3. **Edge kernels** — a new target under `edge/kernel/<target>/` needs:
   boot code, a POKE frame handler (see `PROTOCOL.md`), and a Makefile.

## Before you open a PR

```bash
npm install
npm test          # all suites must pass (500+ tests)
```

If you touched a kernel, note in the PR whether you verified on QEMU
(`make qemu` where available) or on real hardware.

## Reporting bugs

Use the Bug Report issue template. For device quirks (a panel that wires
its touch controller differently, a PHY that needs a different init), the
register-level evidence you gathered is the most valuable part — include it.
