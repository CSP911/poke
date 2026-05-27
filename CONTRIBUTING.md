# Contributing to POKE

Thank you for your interest in contributing to POKE — Protocol for Open Kernel Execution.

## Ways to Contribute

- **Device Profiles**: Add profiles for hardware you have access to
- **Bug Reports**: Found something broken? Open an issue
- **Code**: Fix bugs, add features, improve the assembler
- **Documentation**: Tutorials, examples, translations
- **Testing**: Test on real hardware (Raspberry Pi, ESP32, etc.)

## Getting Started

```bash
# Clone
git clone https://github.com/CSP911/poke.git
cd poke

# Install hub dependencies
npm install

# Build x86 edge (requires i686-elf-gcc, nasm)
make

# Run QEMU edge
make run

# Start hub (needs ANTHROPIC_API_KEY in .env)
node hub.js
```

## Development Setup

### Prerequisites

- Node.js 18+
- QEMU (`brew install qemu` or `apt install qemu-system`)
- Cross-compilers:
  - x86: `i686-elf-gcc`, `nasm`
  - ARM64: `aarch64-elf-gcc`

### Project Structure

```
poke/
  kernel.c          x86 bare-metal kernel
  arm/kernel.c      ARM64 bare-metal kernel
  hub.js            Hub server (LLM + agent loop)
  asm.js            Built-in x86 assembler (no nasm needed)
  profiles/         Device profile database
  mobile.html       Browser-based mobile edge
```

## Submitting Changes

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-change`)
3. Make your changes
4. Test your changes
5. Commit with a clear message
6. Push to your fork
7. Open a Pull Request

### Commit Messages

```
feat: add VirtIO GPU device profile
fix: TCP checksum calculation for large payloads
docs: add Raspberry Pi setup tutorial
test: add asm.js encoding tests for jump instructions
```

### Code Style

- JavaScript: 2-space indent, single quotes, no semicolons optional
- C (kernel): 4-space indent, K&R braces
- Assembly: NASM syntax for x86, GNU as for ARM64

## Adding a Device Profile

Device profiles are JSON files in `profiles/`. To add a new one:

1. Identify the device (vendor:device ID, type, registers)
2. Create `profiles/VENDORID_DEVICEID.json`:

```json
{
  "vendor_device": "XXXX:YYYY",
  "name": "Device Name",
  "type": "network|graphics|audio|sensor|gpio",
  "arch": "i386",
  "bar0": { "address": "0x...", "isIO": false },
  "registers": {
    "REG_NAME": { "offset": "0x00", "desc": "What it does" }
  },
  "operations": [
    {
      "name": "read_something",
      "desc": "What this operation does",
      "asm": "BITS 32\nmov eax, ...\nret",
      "returns": "Description of return value"
    }
  ]
}
```

3. Test it: `node probe.js` or through the hub relay
4. Submit a PR

## Reporting Bugs

Open an issue with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Environment (OS, QEMU version, Node.js version)

## Questions?

Open a Discussion on GitHub or reach out via Issues.
