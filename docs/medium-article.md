# The OS Was Built for Humans. AI Doesn't Need It.

## I built a protocol where AI generates machine code and executes it on bare metal — no OS, no drivers, no apps, no permanent binaries.

---

Every piece of software you've ever used runs on an operating system. Windows, Linux, macOS, Android — they all exist for one reason: **humans can't talk to hardware directly.**

So we built layers. Decades of layers.

```
1970s  Operating Systems       → manage memory, files, processes
1980s  Device Drivers          → translate hardware differences
1990s  Libraries & Frameworks  → reuse common code
2000s  App Stores              → package and distribute software
```

Each layer was a bandage for a human limitation. We couldn't remember register addresses, so we got drivers. We couldn't rewrite sorting algorithms every time, so we got libraries. We couldn't distribute binaries efficiently, so we got app stores.

**But what if the machine could do all of that itself?**

In 2026, LLMs can generate machine code on the fly. They understand hardware specifications. They interpret natural language. They don't need pre-built abstractions because they can build any abstraction in milliseconds and throw it away.

I asked myself: if the LLM is the driver, the compiler, the library, and the app — what's left for the OS to do?

Nothing.

So I killed it.

---

## Meet POKE: Protocol for Open Kernel Execution

POKE is a protocol where AI directly controls hardware. Here's what it looks like:

```
Me:    "What hardware is connected to this machine?"
Hub:    LLM writes a PCI bus scanner in C — 319 bytes
Hub:    Cross-compiles to flat binary
Hub:    Injects into bare-metal x86 device via HTTP
Edge:   CPU scans PCI config space directly. No OS involved.
Edge:   Returns raw register values
Hub:    LLM interprets: "8 devices found — Intel NIC, VGA, VirtIO RNG..."
```

No operating system. No drivers. No apps. A 24KB kernel that only knows how to boot, listen on HTTP, receive bytes, and execute them.

The LLM does everything else.

---

## The Death of Permanent Software

Here's the part that keeps me up at night.

Every program on your computer is a **permanent binary**. Someone wrote it, compiled it, shipped it. It sits on your disk. It gets updated every few weeks. It accumulates cruft, vulnerabilities, compatibility issues.

POKE binaries are different. They're **ephemeral**.

```
Traditional:
  Write code → compile → install → store on disk → run for months
  Bug found? Patch → recompile → redistribute → hope everyone updates

POKE:
  Speak → LLM generates binary → inject → execute → gone
  Next request? Generate fresh binary from scratch
```

A POKE binary lives for milliseconds. It's generated on demand, tailored to the exact request, executed once, and discarded. There is nothing to install, nothing to update, nothing to patch, nothing to exploit.

Think about what this means:

- **No software updates.** Every execution is freshly generated from the latest LLM knowledge.
- **No version conflicts.** There's no "installed version." Just the current intent.
- **No attack persistence.** Malware can't hide in a binary that doesn't exist after execution.
- **No bloat.** Each binary contains exactly the code needed. Not a byte more.

The permanent binary — the fundamental unit of software for 60 years — becomes disposable.

---

## "But Can an LLM Actually Generate Machine Code?"

I tested this with 20 tasks across three difficulty levels — easy (single operations), medium (multi-instruction), and hard (loops and complex logic). Two models, zero assemblers, raw hex bytes only:

```
                          Opus 4.6    Haiku 4.5
                          ─────────   ─────────
EASY (6 tests)
  return 0                   ✅          ✅
  return 42                  ✅          ✅
  return 0xDEAD              ✅          ✅
  2 + 3                      ✅          ✅
  100 - 37                   ✅          ✅
  10 * 7                     ✅          ✅
                             6/6         6/6

MEDIUM (8 tests)
  50 / 2 (division)          ✅          ✅
  255 AND 0x0F               ✅          ✅
  0xF0 OR 0x0F               ✅          ✅
  1 << 8 (shift)             ✅          ✅
  1024 >> 3                  ✅          ✅
  negate -5                  ✅          ✅
  (3 + 7) * 2               ✅          ✅
  100 - 30 - 20 - 10        ✅          ✅
                             8/8         8/8

HARD (6 tests)
  2^10 (shifts)              ✅          ✅
  factorial(5) loop          ✅          ❌
  sum 1..10 loop             ❌          ❌
  fibonacci(10) loop         ✅          ❌
  popcount(0xFF)             ❌          ❌
  max(37, 92)                ❌          ❌
                             3/6         1/6

TOTAL                       17/20 (85%) 15/20 (75%)
```

Easy and medium: **100% on both models**. The LLM knows exactly how to encode `mov eax, imm32` as `B8` + little-endian, that `idiv` needs `cdq`, that shifts use `C1 E0`. No hesitation, no errors.

Hard tasks (loops, conditionals, multi-register coordination): this is where it gets interesting. Opus nailed factorial and fibonacci — generating correct loop structures in raw bytes, including relative jump offsets. It failed on popcount and comparison, where the byte-level jump offset calculation was wrong by 1-2 bytes.

The takeaway: **the LLM is already a working compiler for 85% of tasks**. The remaining 15% involves complex relative addressing that will improve with each model generation. For POKE, the built-in `asm.js` assembler (300 lines of JS, 45 tests, byte-identical to nasm) covers this gap perfectly. But the gap is closing.

---

## How It Actually Works

POKE has two components: a **Hub** (the brain) and **Edges** (the body).

The Hub is a Node.js server with an LLM agent loop. It receives natural language, decides what to do, generates machine code, and sends it to edges. It runs an autonomous tool-use loop — similar to how Claude Code or Codex work — choosing from tools like `execute_x86`, `fetch_url`, `network_read_mac`, and `build_and_deploy`.

The Edge is a bare-metal kernel. I wrote it from scratch — bootloader in assembly (Real Mode → Protected Mode), TCP/IP stack in C, HTTP server, code injection buffer. That's it. 24KB. It boots, listens, executes whatever bytes arrive, and returns the result.

```
                    ┌──────────────┐
                    │ Human/Voice  │
                    └──────┬───────┘
                           │ natural language
                    ┌──────▼───────┐
                    │   Hub (LLM)  │
                    │              │
                    │  Agent Loop  │ ← observe, think, act, check
                    │  asm.js      │ ← assembly → bytes (300 lines)
                    │  Guard Rail  │ ← scans for dangerous opcodes
                    └──┬───┬───┬──┘
                       │   │   │
                    ┌──▼┐ ┌▼──┐ ┌▼────┐
                    │x86│ │ARM│ │Phone│
                    └───┘ └───┘ └─────┘
                     bare   bare  browser
                     metal  metal
```

The Hub also supports voice — I tested it from a real phone. You speak in Korean, Web Speech API converts to text, the Hub processes it, generates assembly, the QEMU edge executes it, and the Hub speaks the answer back. The entire chain works over WiFi.

---

## Device Profiles: How AI Learns Hardware

An LLM can generate machine code, but it needs to know *which* registers to hit. That's where device profiles come in.

A profile is a JSON file describing a hardware device:

```json
{
  "vendor_device": "8086:100E",
  "name": "Intel 82540EM (e1000)",
  "type": "network",
  "bar0": { "address": "0xfebc0000" },
  "registers": {
    "STATUS": { "offset": "0x0008", "desc": "Device Status" },
    "RAL":    { "offset": "0x5400", "desc": "MAC Address Low" }
  },
  "operations": [
    {
      "name": "read_mac",
      "desc": "Read MAC address",
      "asm": "BITS 32\nmov ebx, 0xfebc0000\nmov eax, [ebx + 0x5400]\nret"
    }
  ]
}
```

When the Hub loads this profile, `read_mac` automatically becomes a tool the LLM agent can call. The LLM doesn't need to know x86 assembly to read a MAC address — it just calls `network_read_mac()`.

**More profiles = more devices POKE can control.** This is the network effect. It's MCP for hardware — where MCP's tool descriptions are JSON-RPC schemas, POKE's tool descriptions are register maps.

---

## Multi-Edge: AI Distributes Work Across Hardware

POKE doesn't parallelize code. It parallelizes **intent**.

When you say "compute 15² on one machine and divide by 2 on another," the LLM:
1. Generates binary A (15² = 225) → sends to Edge 1
2. Takes the result, generates binary B (225 / 2) → sends to Edge 2
3. Both execute on bare metal simultaneously

I benchmarked this with two QEMU instances running 200M loop iterations:

```
Single edge:   349ms
Two edges:     175ms
Speedup:       1.99x (near theoretical maximum)
```

The LLM is the scheduler. It understands data dependencies at the semantic level — not by analyzing bytecode, but by understanding what the human asked for. This is a fundamentally different approach to parallel computing.

---

## Security: Three-Layer Guard Rail

Injecting arbitrary machine code into hardware sounds terrifying. It should. That's why POKE has three layers of protection:

**Layer 1 — asm.js (Hub side):** Scans every assembled binary for dangerous opcodes (HLT halts the CPU, CLI disables interrupts), dangerous I/O ports (system reset port), and writes to protected memory regions (interrupt vector table, kernel code). Blocks oversized binaries (>16KB).

**Layer 2 — Hub compiler:** Validates before transmitting. Rejected binaries never reach the edge. All rejections are logged.

**Layer 3 — Kernel (Edge side):** Last defense. The bare-metal kernel scans the code buffer before executing. If it finds HLT or CLI bytes, it returns `REJECTED: dangerous opcode detected` and does not execute.

Is this bulletproof? No. The honest answer is that bare-metal code injection will always be dangerous. But the guard rail catches the most common failure modes — LLM hallucination, accidental HLT, runaway loops — and the ephemeral nature of POKE binaries means even a successful attack can't persist.

---

## Try It Yourself

```bash
git clone https://github.com/CSP911/poke.git
cd poke
echo "ANTHROPIC_API_KEY=your-key" > .env
docker compose up --build

# Then:
curl -X POST http://localhost:3333/relay \
  -H 'Content-Type: application/json' \
  -d '{"from":"x86-edge","command":"calculate 100 * 7"}'

# → { "result": "eax=700" }
```

Four commands. A bare-metal OS boots inside Docker, the Hub connects to Claude, and you're injecting machine code via natural language.

Open `http://localhost:3333` in your browser for the voice UI. Yes, you can literally talk to bare metal.

---

## What's Next

POKE is Apache 2.0 open source. The immediate roadmap:

- **Raspberry Pi deployment** — real hardware, real GPIO, real LEDs
- **Device profile marketplace** — community-contributed hardware profiles
- **ARM native code generation** — currently x86 is primary
- **Distributed compute** — task splitting across many edges

The longer-term vision: **"POKE compatible"** becomes a label on hardware, like "Wi-Fi certified." Manufacturers publish a device profile instead of building an app. Users say what they want, the AI figures out the rest.

---

## The Punchline

For 50 years, we've been building layers between humans and hardware. Operating systems, drivers, compilers, frameworks, app stores — all because humans couldn't speak machine.

Now machines can speak machine.

The layers are dissolving. The OS was always a translator. When both sides speak the same language, the translator goes home.

**GitHub:** https://github.com/CSP911/poke

**License:** Apache 2.0 | **Tests:** 80 | **Dependencies:** 2 (anthropic SDK, dotenv)
