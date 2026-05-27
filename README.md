<p align="center">
  <h1 align="center">POKE</h1>
  <p align="center"><strong>Protocol for Open Kernel Execution</strong></p>
  <p align="center">
    <a href="#quick-start">Quick Start</a> &middot;
    <a href="#why-poke-exists">Why</a> &middot;
    <a href="#architecture">Architecture</a> &middot;
    <a href="#protocol">Protocol</a> &middot;
    <a href="CONTRIBUTING.md">Contributing</a>
  </p>
</p>

---

**MCP connects AI to software. POKE connects AI to hardware.**

```
You: "Calculate 2 + 3"
  → Hub (LLM): generates x86 machine code
  → Edge (bare metal): executes raw bytes on CPU
  → Result: eax=5
```

No operating system. No drivers. No apps. Just AI talking directly to hardware.

---

## Why POKE Exists

### The OS was built for humans, not for AI.

Operating systems exist because humans needed abstraction. We couldn't write raw machine code for every task, so we built layers:

```
1960s  Hardware only          Humans write machine code by hand
1970s  Operating Systems      Abstraction layer — files, processes, memory management
1980s  Drivers                Hardware abstraction — one interface for many devices
1990s  Libraries & Frameworks Code reuse — don't reinvent the wheel
2000s  App Stores             Distribution — package everything into apps
2020s  AI / LLM               Machines that understand language and generate code
```

Every layer was created because **humans couldn't do it themselves**. The OS, drivers, libraries — they're all **pre-built solutions for human limitations**.

But LLMs don't have those limitations. An LLM can:
- Generate machine code on the fly (no need for pre-compiled binaries)
- Understand any hardware spec (no need for pre-written drivers)
- Interpret natural language (no need for app UIs)

**If "pre-building" is unnecessary, the OS is unnecessary.**

What remains is the bare minimum: hardware + network + a protocol to inject and execute code.

That's POKE.

```
Traditional:  Human → App → Framework → OS → Driver → Hardware
POKE:         Human → LLM → Machine Code → Hardware
```

### POKE vs MCP

| | MCP | POKE |
|---|---|---|
| Connects AI to | Software tools | Hardware devices |
| Execution | API calls (JSON-RPC) | Machine code injection |
| Abstraction | High | None (bare metal) |
| Controls | Apps, databases, APIs | Registers, GPIO, peripherals |
| Requires | Tool server | Edge runtime + device profile |

MCP gives AI hands in the software world. POKE gives AI hands in the physical world.

---

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/CSP911/poke.git
cd poke
echo "ANTHROPIC_API_KEY=your-key-here" > .env

docker compose up --build
```

That's it. Three services start automatically:

| Service | Port | Description |
|---------|------|-------------|
| Edge | 8080 | Bare-metal x86 OS running in QEMU |
| Hub | 3333 | LLM agent server |
| Setup | — | Auto-registers edge with hub |

### Try it

```bash
# Natural language → machine code → bare-metal execution
curl -X POST http://localhost:3333/relay \
  -H 'Content-Type: application/json' \
  -d '{"from":"x86-edge","command":"calculate 100 * 7"}'

# → { "result": "eax=700" }
```

```bash
# Direct machine code injection (no LLM)
printf '\xB8\x02\x00\x00\x00\x83\xC0\x03\xC3' | \
  curl http://localhost:8080/poke --data-binary @-

# → eax=5  (mov eax,2; add eax,3; ret)
```

```bash
# Open voice UI in browser
open http://localhost:3333
# Tap the phone icon → speak → hear response
```

### Manual Setup

```bash
# Prerequisites: nasm, i686-elf-gcc, qemu, node.js
make                    # Build x86 kernel
make run                # Start QEMU edge (port 8080)
node hub.js             # Start hub (port 3333)

# Register edge
curl -X POST http://localhost:3333/enroll \
  -H 'Content-Type: application/json' \
  -d '{"node_id":"x86","endpoint":"http://localhost:8080","arch":"i386"}'
```

---

## Architecture

```
                    ┌──────────────────┐
                    │   Human / Voice  │
                    └────────┬─────────┘
                             │ natural language
                    ┌────────▼─────────┐
                    │                  │
                    │    Hub (LLM)     │  Node.js + Claude API
                    │                  │
                    │  ┌────────────┐  │
                    │  │ Agent Loop │  │  observe → think → act → check
                    │  │            │  │
                    │  │  Tools:    │  │  execute_x86, execute_arm,
                    │  │            │  │  draw_image, fetch_url,
                    │  │            │  │  device_read_mac, ...
                    │  └────────────┘  │
                    │                  │
                    │  ┌────────────┐  │
                    │  │  asm.js    │  │  Built-in assembler (300 lines JS)
                    │  │            │  │  Assembly text → raw bytes
                    │  │  Guard     │  │  Scans for HLT, CLI, bad ports
                    │  │  Rail      │  │  Rejects dangerous code
                    │  └────────────┘  │
                    │                  │
                    └──┬─────┬─────┬───┘
                       │     │     │
              ┌────────▼┐ ┌──▼───┐ ┌▼────────┐
              │ x86 Edge│ │ ARM  │ │ Mobile  │
              │ (QEMU)  │ │ Edge │ │ Edge    │
              │         │ │      │ │(Browser)│
              │ TCP/IP  │ │ UART │ │ Web     │
              │ HTTP    │ │Serial│ │ Speech  │
              │ /poke   │ │ POKE │ │ API     │
              └─────────┘ └──────┘ └─────────┘
                bare metal  bare metal  browser
```

### How the Agent Loop Works

The hub doesn't follow a fixed pipeline. It runs an **autonomous agent loop** — the LLM decides what tools to use, in what order, and retries on failure:

```
User: "Read the e1000 network card's MAC address"

  → Agent examines available tools
  → Step 1: list_profiles()           — discovers e1000 at BAR0=0xfebc0000
  → Step 2: read_profile("8086:100E") — gets register offsets
  → Step 3: network_read_mac()        — auto-generated tool, reads hardware
  → Step 4: reply_text()              — "MAC: 52:54:00:12:34:56"
```

The LLM autonomously chains: external APIs, device operations, compute tasks, and image generation — in a single conversation turn.

### Device Profiles = Auto-Generated Tools

Device profiles describe hardware (registers, capabilities). When loaded, each operation becomes an agent tool **automatically**:

```
profiles/8086_100E.json:
  operations: [
    { name: "read_mac",    asm: "..." },
    { name: "read_status", asm: "..." }
  ]

  → Auto-generated tools:
    network_read_mac()     — LLM can call directly
    network_read_status()  — No assembly knowledge needed
```

More profiles = more tools = more devices POKE can control.

### Three-Layer Guard Rail

Every binary is scanned before execution:

```
Layer 1: asm.js (hub)
  Scans assembled bytes for dangerous opcodes (HLT, CLI, WBINVD),
  dangerous I/O ports (system reset), writes to protected memory.

Layer 2: hub/compiler.js (hub)
  Validates before sending to edge.
  Rejected binaries are never transmitted.

Layer 3: kernel.c (edge, bare metal)
  Last defense. Scans code buffer at the metal level.
  Returns "REJECTED" HTTP response if dangerous code found.
```

---

## Protocol

Full specification: [PROTOCOL.md](PROTOCOL.md)

### Core Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/enroll` | Register an edge device |
| GET | `/nodes` | List connected edges |
| POST | `/relay` | Natural language → agent loop → execute |
| POST | `/run` | Direct LLM → assembly → execute |
| POST | `/draw` | Generate image → send to edge display |
| POST | `/voice` | Audio → STT → relay pipeline |
| POST | `/poke-raw` | Send raw hex bytes to edge |
| GET | `/health` | Edge health check |

### Binary Protocols

```
POKE (code injection):
  POST /poke + raw bytes → edge executes → returns result

IMG (image):
  "IMG" (3B) + width (2B LE) + height (2B LE) + RGB pixels

Serial (ARM):
  "POKE" (4B) + length (4B LE) + payload
  Commands: EXEC, PING, INFO, TONE
```

---

## Project Structure

```
poke/
├── kernel.c              x86 bare-metal kernel
│                         (boot → TCP/IP → HTTP → code injection)
├── boot.asm              Bootloader (Real Mode → Protected Mode)
├── kernel_entry.asm      BSS init + C entry point
├── linker.ld             Linker script
├── Makefile              Build + QEMU launch
│
├── arm/                  ARM64 bare-metal kernel
│   ├── kernel.c          UART + serial protocol + audio
│   ├── start.S           ARM64 entry
│   └── Makefile
│
├── hub.js                Hub entry point
├── hub/                  Hub modules
│   ├── server.js         HTTP routing + auth + validation
│   ├── agent.js          Agent loop + tool definitions
│   ├── llm.js            LLM API calls
│   ├── compiler.js       Assembly compilation + guard rail
│   ├── transport.js      Edge communication (HTTP, TCP)
│   ├── nodes.js          Node registry + device profiles
│   └── logger.js         Structured logging
│
├── asm.js                Built-in x86 assembler (replaces nasm)
│                         Zero external dependencies
│
├── mobile.html           Browser edge (voice UI + canvas)
├── profiles/             Device profile database
│
├── Dockerfile.hub        Hub container
├── Dockerfile.edge       Edge container (QEMU)
├── docker-compose.yml    One-command startup
│
├── PROTOCOL.md           Protocol specification
├── DEPLOY.md             Production deployment guide
├── SECURITY.md           Security policy
├── CONTRIBUTING.md       Contribution guide
└── LICENSE               Apache 2.0
```

---

## What Makes POKE Different

### LLM as the Compiler

POKE doesn't need gcc, clang, or any traditional compiler. The LLM generates machine code directly:

```
Claude Opus 4.6 — raw byte generation test:

  "2 + 3"          → B8 02 00 00 00 83 C0 03 C3        → eax=5     ✅
  "10 * 7"         → B8 0A 00 00 00 6B C0 07 C3        → eax=70    ✅
  "100 - 37"       → B8 64 00 00 00 83 E8 25 C3        → eax=63    ✅
  "255 AND 0x0F"   → B8 FF 00 00 00 25 0F 00 00 00 C3  → eax=15    ✅
  "1 << 8"         → B8 01 00 00 00 C1 E0 08 C3        → eax=256   ✅
  "50 / 2"         → B8 32 ... 99 F7 F9 C3             → eax=25    ✅
  "return 0xDEAD"  → B8 AD DE 00 00 C3                 → eax=57005 ✅

  Score: 7/7 (100%) — no assembler used
```

For complex code, the built-in `asm.js` (300 lines of JS) handles assembly-to-bytes conversion. No nasm or external tools required.

### Task-Level Parallelism

POKE doesn't parallelize binaries — it parallelizes **intent**:

```
User: "Compute 15² on x86, then divide by 2 on ARM"

  → Hub decomposes the task semantically
  → Step 1: x86 edge computes 15² = 225        (171ms)
  → Step 2: ARM edge computes 225 / 2 = 112    (175ms)
  → Both run on different bare-metal CPUs

Benchmark (50M iterations × 4, split across 2 edges):
  Single:   349ms
  Parallel: 175ms
  Speedup:  1.99x
```

### External Data + Hardware in One Binary

The hub fetches external data and embeds it directly into the binary:

```
User: "If Seoul temperature > room sensor, turn on AC"

  Hub: fetch(weather API) → 25°C
  Hub: generates binary:
    int outside = 25;                      // ← hub-injected data
    int inside = read_sensor(0xfebc0000);  // ← hardware register
    if (outside > inside)
        gpio_set(PIN_AC, 1);              // ← hardware control

  Edge: executes on bare metal. No OS involved.
```

---

## Roadmap

- [x] x86 bare-metal OS (boot → protected mode → TCP/IP → HTTP → code exec)
- [x] ARM64 bare-metal OS (UART + serial protocol)
- [x] Hub with LLM agent loop (tool use, multi-step reasoning)
- [x] Built-in assembler (`asm.js`, zero dependencies)
- [x] Device profiles → auto-generated agent tools
- [x] Three-layer guard rail (asm.js + hub + kernel)
- [x] Voice pipeline (STT → LLM → execute → TTS)
- [x] Multi-edge orchestration + parallel execution
- [x] Docker one-command setup
- [x] 80 automated tests + CI
- [ ] Real hardware deployment (Raspberry Pi, ESP32)
- [ ] Device profile marketplace
- [ ] ARM native code generation
- [ ] Distributed compute (task splitting)
- [ ] Web dashboard for edge monitoring

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The easiest way to start:

1. **Add a device profile** — if you have hardware, scan it and submit a profile
2. **Test on real devices** — Raspberry Pi, STM32, ESP32
3. **Extend asm.js** — add more x86 instruction encodings
4. **Write tutorials** — "Control an LED with POKE in 5 minutes"

---

## License

[Apache 2.0](LICENSE)

---

<p align="center">
  <em>"The future doesn't need an operating system."</em>
</p>
