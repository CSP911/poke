# POKE

**Protocol for Open Kernel Execution**

MCP connects AI to software. POKE connects AI to hardware.

```
Human: "2 더하기 3 계산해줘"
  → Hub (LLM): generates x86 assembly
  → Edge (bare metal): executes machine code
  → Result: eax=5
  → Hub (TTS): "결과는 5입니다"
```

POKE is a protocol that lets AI directly control any hardware — no OS, no drivers, no apps. The hub (LLM brain) interprets natural language, generates machine code, and injects it into bare-metal edge devices for execution.

---

## Architecture

```
         Voice / Text
              |
         Hub (LLM)          ← Brain: STT, planning, code generation, TTS
        /     |      \
   x86 Edge  ARM Edge  Mobile Edge
   (QEMU)    (QEMU)    (Browser)
```

- **Hub**: Node.js server with LLM. Interprets commands, generates assembly, orchestrates edges.
- **Edge**: Bare-metal runtime. Receives machine code via HTTP or serial, executes it, returns results.
- **Device Profile**: JSON description of a device's registers, peripherals, capabilities. The more profiles, the more devices POKE can control.

## Quick Start

```bash
# 1. Build x86 edge
make

# 2. Start QEMU edge
make run    # x86 on port 8080

# 3. Start hub (needs ANTHROPIC_API_KEY in .env)
node hub.js # hub on port 3333

# 4. Register edge
curl -X POST http://localhost:3333/enroll \
  -H 'Content-Type: application/json' \
  -d '{"node_id":"x86-qemu","endpoint":"http://localhost:8080","arch":"i386"}'

# 5. Execute via natural language
curl -X POST http://localhost:3333/relay \
  -H 'Content-Type: application/json' \
  -d '{"from":"test","command":"calculate 100 * 7"}'
# → { "result": "eax=700" }
```

## Raw Machine Code

```bash
# Direct injection (no LLM)
# 2 + 2
printf '\xB8\x02\x00\x00\x00\x83\xC0\x02\xC3' | \
  curl http://localhost:8080/poke --data-binary @-
# → eax=4
```

## Voice Pipeline

```bash
# Voice test: speech → ARM edge → hub (STT+LLM) → x86 (compute) → TTS
node voice_test.js "100 곱하기 7"
```

Browser voice (Web Speech API):
```
open http://localhost:3333   # tap phone icon, speak
```

## Multi-Edge

```bash
# Run 2 x86 edges simultaneously
cp poke.img poke2.img
qemu-system-i386 -drive format=raw,file=poke2.img -m 64M \
  -device e1000,netdev=net0 \
  -netdev user,id=net0,hostfwd=tcp::8082-:80 \
  -display none -monitor none

# ARM edge
cd arm && make run  # on port 8081
```

## Project Structure

```
poke/
  boot.asm          x86 bootloader (Real Mode → Protected Mode)
  kernel_entry.asm  BSS init + C entry
  kernel.c          Kernel: VGA + keyboard + e1000 + TCP/IP + HTTP + code exec + IMG
  linker.ld         Linker script (0x1000)
  Makefile          Build + QEMU

  arm/
    start.S         ARM64 entry
    kernel.c        UART console + network + audio + code exec
    Makefile         Build + QEMU (3 serial ports)

  hub.js            Hub server: enroll, relay, run, draw, voice, dashboard
  mobile.html       Browser edge: voice call UI + canvas + relay
  voice_test.js     Voice pipeline test script

  profiles/         Device profile DB (auto-discovered)
  debates/          Architecture decisions and vision docs
  PROTOCOL.md       Protocol specification
```

## vs MCP

| | MCP | POKE |
|---|---|---|
| Connects AI to | Software tools | Hardware devices |
| Execution | API calls | Machine code injection |
| Abstraction | High (JSON-RPC) | None (bare metal) |
| Requires | Tool server | Edge runtime + device profile |
| Controls | Apps, databases, services | Registers, GPIO, peripherals |

## Status

- [x] x86 bare-metal OS (boot → protected mode → TCP/IP → HTTP)
- [x] ARM64 bare-metal OS (UART network + audio)
- [x] Hub with LLM code generation (natural language → assembly)
- [x] Multi-edge orchestration (x86 + ARM + mobile)
- [x] Voice pipeline (STT → LLM → execute → TTS)
- [x] Device auto-discovery and profiling (PCI scan → LLM probe)
- [x] Image protocol (IMG + pixel data)
- [x] Streaming protocol (FRM frames over TCP)
- [ ] Real device deployment (Raspberry Pi)
- [ ] Device profile marketplace
- [ ] Distributed compute (task splitting across edges)
- [ ] ARM native code generation (currently x86 only)
