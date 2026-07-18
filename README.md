<p align="center">
  <h1 align="center">POKE</h1>
  <p align="center"><strong>Protocol for Open Kernel Execution</strong></p>
  <p align="center">by <strong>Orvian</strong> — from Orbit and Via, the path that carries intelligence into machines.</p>
  <p align="center">
    <a href="https://www.npmjs.com/package/@orvian/poke"><img src="https://img.shields.io/npm/v/@orvian/poke" alt="npm"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue" alt="license"></a>
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

### The End of Permanent Binaries

Every program you use today is a **permanent binary** — compiled once, installed, stored on disk, updated periodically. This made sense when compilation was expensive and humans needed stable, repeatable software.

But POKE flips this model. Binaries become **ephemeral**:

```
Traditional (permanent):
  Write code → compile → install → store on disk → run forever
  Update? Recompile → reinstall → restart

POKE (ephemeral):
  Speak → LLM generates binary → inject → execute → discard
  Next request? Generate a new binary from scratch
```

A POKE binary lives for **milliseconds**. It's generated on demand, tailored to the exact request, executed once, and thrown away. There's nothing to install, nothing to update, nothing to patch.

This changes everything:
- **No software updates.** Every execution is freshly generated.
- **No version conflicts.** There's no installed version — just the current intent.
- **No attack persistence.** Malware can't persist in a binary that doesn't exist after execution.
- **No bloat.** Each binary contains exactly the code needed — nothing more.

The future isn't permanent software running on an OS. It's **volatile binaries generated in real-time by AI, executed directly on hardware, and discarded.**

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

### 1. HEX Autonomous Federation (recommended)

Talk to a self-operating multi-site system. 3 hubs, 9 bare-metal edges, 1 LLM orchestrator — no hardware needed:

```bash
git clone https://github.com/CSP911/poke.git
cd poke
npm install

# Configure API key
cp .env.example .env
# Edit .env — add your Anthropic API key:
#   ANTHROPIC_API_KEY=sk-ant-your-key-here
#   HUB_SECRET=poke-secret

# Run autonomous multi-hub federation
node hex/autonomous.js
```

The system boots 9 QEMU bare-metal edges across 3 sites (Office, Factory, Warehouse), starts 3 independent hubs, and begins autonomous monitoring. No human input needed — the LLM detects anomalies and executes corrective actions:

```
═══ Cycle 1 ═══
  Office       entrance:26.2C  workspace:27.2C  server-room:26C
  Factory      line-1:26C  line-2:26.8C  quality:27C
  Warehouse    dock:25.6C  storage:27.4C  cold-room:26C
  HEX: All clear ✓

═══ Cycle 2 ═══
  server-room: 31C!
  HEX: "위험! 쿨링팬 켠다"
  >>> ACTION: office-server-room GPIO 0 = 1 — {"pin":0,"value":1} ✅
```

### Other modes

```bash
node hex/cli.js              # Interactive chat with 10 edges
node hex/federation.js       # Multi-hub with @hub targeting
node hex/simulator.js        # Dynamic events simulation
node hex/scenarios.js        # Run 8 automated LLM decision tests
```

### 2. ESP32-C3 Serial Demo

Real hardware (ESP32-C3 RISC-V) connected via USB serial — hub compiles RISC-V assembly, sends binary over serial, ESP32 executes bare-metal and returns the result:

<p align="center">
  <img src="demo/serial-pipeline.gif" alt="ESP32-C3 Serial Pipeline Demo" width="720">
</p>

### Configuration

| Variable | Required | Description |
|----------|:--------:|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key ([get one here](https://console.anthropic.com/)) |
| `HUB_SECRET` | No | Bearer token for hub authentication |
| `LOG_LEVEL` | No | `debug`, `info` (default), `warn`, `error` |
| `PORT` | No | Hub port (default: 3333) |

> **Platform:** macOS (Apple Silicon / Intel), Linux x86_64. Requires QEMU and cross-compilers for kernel builds.

### Example Commands

```
"calculate 100 * 7"                          → assembly → bare-metal → eax=700
"what hardware is connected?"                → PCI scan → C binary → device list
"read the network card MAC address"          → auto-generated tool → 52:54:00:12:34:56
"get Seoul weather, convert to Fahrenheit"   → fetch API + CPU compute → 25°C = 77°F
"read temperature from all sensors"          → distributed sensor query
"1234567 * 7654321"                          → 32-bit overflow proves real CPU execution
```

Each command shows the **execution flow**: which tools were called, what assembly was generated, which edge executed it, and the result.

---

## Architecture

```mermaid
graph TB
    User["Human / Voice"]

    subgraph Hub["Hub (Node.js + LLM)"]
        Agent["Agent Loop<br/>observe → think → act → check"]
        Tools["Tools<br/>execute_x86 | execute_arm | execute_armv6<br/>draw_image | fetch_url<br/>device_read_mac | ..."]
        ASM["asm.js | asm_armv6.js<br/>Built-in Assembler<br/>(300 lines, zero deps)"]
        Guard["Guard Rail<br/>Scans for HLT, CLI<br/>Blocks dangerous code"]
        Agent --> Tools
        Tools --> ASM
        ASM --> Guard
    end

    subgraph Edges["Edge Devices (bare metal)"]
        X86["x86 Edge<br/>QEMU / Real HW<br/>TCP/IP + HTTP"]
        ARM["ARM64 Edge<br/>Pi 4 / QEMU<br/>PL011 UART"]
        ARMv6["ARMv6 Edge<br/>Pi Zero W<br/>UART Serial"]
        RV["RISC-V Edge<br/>ESP32-C3<br/>USB Serial"]
        Mobile["Mobile Edge<br/>Browser<br/>Web Speech API"]
    end

    User -->|natural language| Hub
    Guard -->|raw bytes| X86
    Guard -->|raw bytes| ARM
    Guard -->|raw bytes| ARMv6
    Guard -->|raw bytes| RV
    Guard -->|image data| Mobile
    X86 -->|eax=result| Hub
    ARM -->|x0=result| Hub
    ARMv6 -->|r0=result| Hub
    RV -->|a0=result| Hub
    Mobile -->|voice input| Hub
```

### How a Request Flows

```mermaid
sequenceDiagram
    actor User
    participant Hub as Hub (LLM)
    participant ASM as asm.js
    participant Guard as Guard Rail
    participant Edge as x86 Edge

    User->>Hub: "calculate 100 * 7"
    Hub->>Hub: Agent Loop: plan task
    Hub->>Hub: LLM generates assembly:<br/>mov eax, 100<br/>imul eax, 7<br/>ret
    Hub->>ASM: assemble("mov eax, 100...")
    ASM-->>Hub: [B8 64 00 00 00 6B C0 07 C3]
    Hub->>Guard: scanBinary(bytes)
    Guard-->>Hub: safe ✓
    Hub->>Edge: POST /poke + raw bytes
    Edge->>Edge: CPU executes at bare metal
    Edge-->>Hub: eax=700
    Hub-->>User: "Result: 700"
```

### Multi-Edge Parallel Execution

```mermaid
graph LR
    Hub["Hub (LLM)"]

    Hub -->|"15² = ?"| E1["x86 Edge #1"]
    Hub -->|"225 / 2 = ?"| E2["x86 Edge #2"]

    E1 -->|"eax=225"| Hub
    E2 -->|"eax=112"| Hub

    Hub -->|"225 + 112 = 337"| Result["Combined Result"]

    style E1 fill:#4CAF50,color:#fff
    style E2 fill:#2196F3,color:#fff
```

### Device Profile → Auto-Generated Tools

```mermaid
graph LR
    Profile["profiles/8086_100E.json<br/>Intel e1000 NIC<br/>BAR0=0xfebc0000"]

    Profile --> T1["network_read_mac()"]
    Profile --> T2["network_read_status()"]

    T1 --> Agent["LLM Agent<br/>calls directly"]
    T2 --> Agent

    Agent -->|"generated bytes"| Edge["x86 Edge"]
    Edge -->|"52:54:00:12:34:56"| Agent

    style Profile fill:#FF9800,color:#fff
    style T1 fill:#4CAF50,color:#fff
    style T2 fill:#4CAF50,color:#fff
```

### The Same Agent Loop as Claude Code / Codex

The hub runs the exact same simple loop that powers Claude Code, Codex, and Devin:

```
while not done:
    observe()   →  read context, check state
    think()     →  LLM decides next action
    act()       →  call a tool
    check()     →  verify result, retry if failed
```

The only difference is **what the tools are**:

```
Claude Code                           POKE
───────────                           ────
tool: edit_file                       tool: execute_x86
params: {                             params: {
  path: "main.py",                      target: "x86-edge",
  content: "print('hi')"                asm_code: "mov eax,2\nadd eax,3\nret"
}                                     }

tool: bash                            tool: build_and_deploy
params: {                             params: {
  command: "npm test"                   target: "x86-edge",
}                                       c_code: "void _start() { ... }"
                                      }

tool: read_file                       tool: network_read_mac
params: {                             params: {
  path: "config.json"                   target: "x86-edge"
}                                     }
```

**The device IS the tool. The assembly IS the parameter.**

The LLM autonomously chains tools in a single turn: fetch external APIs, read hardware registers, compute across edges, generate images — deciding on its own what to do and retrying on failure.

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

### Hub Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/enroll` | Register edge (auto-triggers incubation) |
| GET | `/nodes` | List connected edges |
| POST | `/relay` | Natural language → agent loop → execute |
| POST | `/run` | Direct LLM → assembly → execute |
| POST | `/goal` | Start goal-based autonomous control |
| POST | `/goal/stop` | Stop active goal |
| GET | `/goal` | Current goal status + cycle history |
| POST | `/scenario/start/:id` | Auto-provision env + run scenario |
| GET | `/incubate/:id` | Trigger device incubation |
| GET | `/asmcache` | List cached assembly templates |
| GET | `/edge/context` | Read context from edge disks |

### Edge Endpoints (bare-metal kernel)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health + virtual registers + monitor status + context count |
| GET | `/pci` | PCI device list (real + virtual sensors) |
| GET | `/store` | Read context entries from disk |
| GET | `/key` | Last keyboard scancode |
| POST | `/poke` | Code injection (dispatches by magic bytes) |

### Binary Protocols (POST /poke body)

```
Raw bytes     → code execution → returns eax value
"CTX" + data  → context store write (persistent disk)
"MON" + data  → deploy condition monitor (4 slots)
"RES" + data  → deploy resident binary (persistent control loop, 4 slots)
"STP" + slot  → stop resident binary
"DIE"         → graceful QEMU shutdown
"IMG" + data  → image display
"STR" + data  → frame streaming
"DRAW" + data → procedural drawing
```

---

## Project Structure

```
poke/
├── kernel/                   Bare-metal kernels (6 architectures)
│   ├── x86/                  x86 (QEMU) — TCP/IP + HTTP + VirtIO
│   ├── arm64/                ARM64 (QEMU virt) — PL011 UART
│   ├── rv32/                 RISC-V 32 (QEMU virt) — NS16550 UART
│   ├── pi0w/                 Pi Zero W (real HW + QEMU) — Mini UART
│   ├── pi4/                  Pi 4 (real HW) — PL011 UART
│   └── esp32c3/              ESP32-C3 (Direct Boot) — USB-Serial/JTAG
│
├── src/                      Built-in assemblers (zero deps)
│   ├── hub.js                Hub entry point
│   ├── asm.js                x86 assembler
│   ├── asm_arm.js            ARM64 assembler
│   ├── asm_armv6.js          ARMv6 assembler (Pi Zero W)
│   └── asm_rv.js             RISC-V assembler (RV32IM)
│
├── hub/                      Hub modules
│   ├── server.js             HTTP routing + auth
│   ├── agent.js              LLM agent loop + tool definitions
│   ├── serial.js             Serial + TCP transport (POKE frame protocol)
│   ├── library.js            Device library — modular matching + auto-incubation
│   ├── compiler.js           Assembly compilation + guard rail
│   ├── transport.js          Edge communication (HTTP)
│   ├── nodes.js              Node registry + health check
│   ├── memory.js             Persistent memory (monthly files, patterns)
│   └── logger.js             Structured logging
│
├── library/                  Device library (17 entries, architecture-neutral)
│   ├── index.json            Device ID → entry mapping (v3.0)
│   ├── esp32c3_base.json     ESP32-C3 chip base
│   ├── bcm2835_base.json     Pi Zero W chip base
│   ├── intel_e1000.json      Intel NIC (register map)
│   ├── virtio_blk.json       VirtIO block device
│   ├── ns16550.json          NS16550 UART
│   ├── dht22.json            Temperature/humidity sensor
│   ├── bmp280.json           Barometric pressure sensor
│   ├── bh1750.json           Ambient light sensor (lux)
│   ├── mq135.json            Air quality gas sensor
│   ├── mpu6050.json          6-axis IMU (accel + gyro)
│   ├── hcsr501.json          PIR motion sensor
│   ├── relay_2ch.json        2-channel relay
│   ├── relay_4ch.json        4-channel relay
│   ├── ir_transmitter.json   IR blaster (NEC/Samsung)
│   ├── ssd1306.json          OLED display (128x64)
│   └── fab_*.json            Factory simulation (cleanroom, etch)
│
├── hex/                      HEX — Hub-Edge eXecutor
│   ├── cli.js                Interactive CLI (10 edges)
│   ├── simulator.js          Live simulation (dynamic events)
│   ├── federation.js         Multi-hub interactive (3 hubs × 3 edges)
│   ├── autonomous.js         Self-operating orchestrator (no human input)
│   ├── scenarios.js          LLM decision tests (8 scenarios)
│   ├── config.json           10-edge configuration
│   └── federation.json       3-hub federation configuration
│
├── test/                     480+ automated tests
│   ├── asm*.test.js          Assembler tests (x86, ARM64, ARMv6, RV32)
│   ├── hub.test.js           Hub endpoint tests
│   ├── library.test.js       Device library tests (207)
│   ├── qemu.test.js          QEMU integration tests (20)
│   └── serial.test.js        ESP32 serial pipeline tests
│
├── web/                      Dashboard UI
├── demo/                     Demo recordings
├── docs/                     Documentation + patents
└── LICENSE                   Apache 2.0
```

---

## What Makes POKE Different

### LLM as the Compiler

No gcc, no clang. The LLM generates machine code directly. Built-in assemblers (`asm.js`, `asm_arm.js`, `asm_armv6.js`, `asm_rv.js`) bridge the gap — 4 architectures, 218 tests, zero external dependencies.

### Autonomous Multi-Site Control (HEX)

The real power: **self-operating infrastructure across multiple sites**.

```
[Orchestrator] — single LLM, full situational awareness
     │ collect: 9 edges via TCP (no LLM, fast)
     │ analyze: 1 LLM call with all data
     │ act: targeted GPIO commands to specific edges
     ↓
Office  server-room 31°C  → set_gpio → fan ON ✅
Factory line-1 26°C       → All clear
Warehouse cold-room 33°C  → set_gpio → fan ON ✅
```

Verified: LLM detects cross-site anomalies, dispatches corrective actions to the correct hub, and monitors recovery — all without human input.

### Sense → Decide → Act Loop

```
Edge monitors condition (bare metal, no OS)
    → threshold exceeded
    → Hub receives event
    → LLM decides corrective action
    → Hub sends new binary to edge
    → Edge state changes
    → Condition clears
```

### Architecture-Neutral Device Library

17 device entries, zero hardcoded assembly. Register maps only — LLM generates the right code for any architecture at runtime:

```json
{
  "id": "intel_e1000",
  "registers": { "STATUS": { "offset": "0x0008" }, "RAL": { "offset": "0x5400" } },
  "operations": [{ "name": "read_mac", "registers": ["RAL", "RAH"] }]
}
```

Same library entry works on x86, ARM64, ARMv6, RISC-V.

---

## Roadmap

- [x] x86 bare-metal OS (boot → protected mode → TCP/IP → HTTP → code exec)
- [x] ARM64 bare-metal OS (UART + serial protocol)
- [x] Hub with LLM agent loop (tool use, multi-step reasoning)
- [x] Built-in x86 assembler (`asm.js`) + ARM64 assembler (`asm_arm.js`)
- [x] Device profiles → auto-generated agent tools (26 profiles)
- [x] Three-layer guard rail (asm.js + hub + kernel)
- [x] Voice pipeline (STT → LLM → execute → TTS)
- [x] Multi-edge orchestration + parallel execution (1.99x speedup)
- [x] Distributed computing (parallel_execute + load balancing)
- [x] HEX CLI + federation + autonomous orchestrator
- [x] Autonomous event loop (edge monitors → LLM auto-decision → corrective action)
- [x] JARVIS memory system (monthly files, keyword index, edge sync)
- [x] VirtIO-blk disk driver + persistent context store (survives reboot)
- [x] Preemptive multitasking (IDT + PIT timer + context switching)
- [x] Resident binaries (persistent control loops, 4 slots, disk-backed)
- [x] PCI device discovery + auto-incubation (sketches → probe → profile)
- [x] Assembly cache (reusable parameterized templates, 14 sketches)
- [x] Goal Mode (LLM-driven autonomous control: plan → monitor → adjust)
- [x] Factory simulation (3 QEMU edges, real shutdown/restart)
- [x] Scenario auto-provisioning (0 → N edges on demand)
- [x] Live trace system (SSE streaming of all hub events)
- [x] Self-extending kernel (module loader from disk)
- [x] 59 automated tests (35 unit + 24 integration)
- [x] ESP32-C3 RISC-V edge (USB serial, POKE frame protocol, bare-metal code exec)
- [x] Internal temperature sensor (ESP-IDF calibrated, LLM-driven read)
- [x] Result validation layer (sanity check + LLM self-correction loop)
- [x] Serial integration tests (16 tests: PING/INFO/GPIO/TEMP + arithmetic + hub API)
- [x] Autonomous event loop — edge-initiated EVNT frames + LLM-driven monitor deploy
- [x] Modular device library — chip base + sensor modules, auto-incubation via LLM
- [x] POKE OS on 6 platforms — x86 + ARM64 + RV32 + ESP32-C3 + Pi Zero W + Pi 4
- [x] RV32 bare-metal kernel (no FreeRTOS) — pure POKE OS for RISC-V
- [x] ESP32-C3 bare-metal POKE kernel — Direct Boot, 3.5KB, no FreeRTOS/ESP-IDF
- [x] Pi Zero W bare-metal kernel — verified on real hardware (2.8KB, ARMv6)
- [x] Pi 4 bare-metal kernel — BCM2711, AArch64, PL011 UART
- [x] ARMv6 mini assembler (asm_armv6.js) — Pi Zero W code injection
- [x] Modular device library — bcm2835_base with WiFi (SDIO) + Camera (CSI) incubating
- [x] 480+ automated tests (unit + QEMU integration)
- [x] HEX multi-hub federation — 3 hubs, 9 edges, autonomous orchestrator
- [x] Architecture-neutral device library (17 entries, v3.0)
- [x] TCP transport for QEMU edges
- [ ] Real hardware multi-site deployment (Pi 4 + ESP32)
- [ ] Device library marketplace
- [ ] Web dashboard for HEX federation

---

## Industry Targets

POKE fits where hardware is fragmented, legacy equipment is alive, and software maintenance costs are high:

| Industry | Use Case | POKE Value |
|----------|----------|------------|
| **Smart Factory** | 50 sensors, 5 protocols → one POKE hub | Integration cost -90% |
| **Building Automation** | HVAC + lighting + access control | 30% energy savings, no BMS replacement |
| **Smart Farm** | Soil/weather sensors, offline operation | Works without internet after deployment |
| **Robotics** | FANUC/ABB/KUKA → vendor-independent control | Same commands, any robot brand |

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
