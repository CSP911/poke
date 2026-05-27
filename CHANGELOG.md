# Changelog

## v0.1.0 — 2026-05-27

Initial release of POKE — Protocol for Open Kernel Execution.

### Features
- x86 bare-metal kernel (boot → protected mode → TCP/IP → HTTP → code injection)
- ARM64 bare-metal kernel (UART network + audio + serial protocol)
- Hub server with LLM agent loop (tool use: compute, device, draw, answer, fetch)
- Built-in x86 assembler (`asm.js`) — zero external dependencies, replaces nasm
- Device profiles with auto-generated agent tools
- Voice pipeline (STT → LLM → assembly → execute → TTS)
- Mobile edge (browser-based, Web Speech API)
- Multi-edge orchestration and parallel execution
- External data injection into bare-metal binaries
- Raw byte generation test (LLM as compiler)

### Supported Architectures
- x86 (i386, 32-bit)
- ARM64 (AArch64)
- Mobile (browser)

### Device Profiles
- Intel 82540EM e1000 (network)
- QEMU stdvga BochsVBE (graphics)
- VirtIO RNG, Balloon
- Intel 440FX, PIIX3 (bridges)
