# Show HN: POKE – AI that generates and injects machine code into bare-metal hardware

**Link:** https://github.com/CSP911/poke

I built a protocol where AI directly controls hardware — no OS, no drivers, no apps. You speak, the LLM generates machine code, injects it into a bare-metal device, and the CPU executes it. The binary lives for milliseconds and is discarded.

## What it does

```
You: "What hardware is connected?"
  → LLM writes a PCI scanner in C (319 bytes)
  → Cross-compiles to flat binary
  → Injects into bare-metal x86 edge via HTTP
  → CPU scans PCI config space directly
  → Returns: 8 devices found (Intel e1000, QEMU VGA, VirtIO RNG...)
  → LLM interprets raw register values and explains
```

No operating system was involved. The edge device runs a 24KB kernel that only knows how to: boot, listen on HTTP, receive bytes, execute them, return results.

## Why

Operating systems exist because humans needed abstraction. We couldn't write machine code for every task, so we built layers: OS → drivers → libraries → apps. Each layer was a pre-built solution for human limitations.

LLMs don't have those limitations. An LLM can generate any machine code on the fly, understand any hardware spec, and interpret natural language. If "pre-building" is unnecessary, the OS is unnecessary.

What remains: bare hardware + network + a protocol to inject code. That's POKE (Protocol for Open Kernel Execution).

## Key ideas

**Ephemeral binaries.** Traditional software is permanent — compiled once, installed, stored, updated. POKE binaries are volatile: generated on demand, executed once, discarded. No installs, no updates, no version conflicts, no attack persistence.

**LLM as compiler.** We tested Claude Opus generating raw x86 bytes directly (no assembler): 7/7 correct. `mov eax, 2; add eax, 3; ret` = `B8 02 00 00 00 83 C0 03 C3`. For complex code, a 300-line JS assembler (`asm.js`) handles it — zero external dependencies, replaces nasm entirely.

**Device profiles = auto-generated tools.** Hardware is described in JSON profiles (registers, capabilities). Each profile operation becomes an LLM agent tool automatically. More profiles = more devices the AI can control. This is MCP for hardware.

**Agent loop, not fixed pipeline.** The hub runs an autonomous agent loop (like Claude Code / Codex). The LLM decides what tools to use: fetch external APIs, read hardware registers, generate binaries, compute on multiple edges — all in one turn. It retries on failure.

**Three-layer guard rail.** Every binary is scanned before execution: (1) asm.js scans for dangerous opcodes (HLT, CLI), bad I/O ports, protected memory writes, (2) hub validates before sending, (3) kernel scans at bare metal before executing.

## Architecture

- **Hub**: Node.js + LLM agent loop with tool use
- **Edge**: Bare-metal kernel (x86 and ARM64) — boots from Real Mode, implements TCP/IP from scratch, serves HTTP, executes injected code
- **Mobile Edge**: Browser with Web Speech API (voice input on real phones)
- **Built-in assembler**: 300 lines of JS, 45 tests passing, byte-identical to nasm output

## Try it

```bash
git clone https://github.com/CSP911/poke.git && cd poke
echo "ANTHROPIC_API_KEY=your-key" > .env
docker compose up --build
curl -X POST http://localhost:3333/relay \
  -H 'Content-Type: application/json' \
  -d '{"from":"x86-edge","command":"calculate 100 * 7"}'
# → { "result": "eax=700" }
```

## What I'm looking for

- Feedback on the protocol design
- Ideas for real hardware targets (Raspberry Pi is next)
- Thoughts on the "ephemeral binary" paradigm
- Security considerations I might have missed
- Anyone interested in contributing device profiles

Built with Claude. Apache 2.0 licensed. 80 tests. CI on GitHub Actions.
