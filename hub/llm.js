/**
 * POKE Hub — all LLM calls
 */

const { log } = require('./logger')

function getClient() {
  const Anthropic = require('@anthropic-ai/sdk')
  return new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY })
}

// ── Plan command (relay) ──
async function planCommand(command, fromId, targetId, nodeList) {
  const client = getClient()

  const nodeDesc = nodeList.map(n =>
    `${n.node_id}: arch=${n.arch}, capabilities=${(n.capabilities||[]).join(',')}`
  ).join('\n')

  const msg = await client.messages.create({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 300,
    system: `You interpret commands sent from one edge device to a hub.
The hub controls multiple edge devices.

Available edges:
${nodeDesc}

The command comes from edge "${fromId}".
${targetId ? `Requested target: "${targetId}". YOU MUST use this target.` : 'No specific target requested.'}

Respond with JSON only:
{
  "type": "compute" | "draw" | "answer" | "device" | "broadcast",
  "target": "node_id to execute on",
  "task": "the specific task description"
}

Types:
- "compute": math/calculation -> target runs assembly code. Target MUST be x86 or ARM edge, NEVER mobile.
- "draw": visual/image -> generate pixel art and show on target.
- "answer": question/conversation that needs NO code execution. Just answer with text.
- "device": hardware control (read sensor, toggle GPIO, control peripheral). Target = the edge with the device.
- "broadcast": show on all edges.

Rules:
- If command is a math calculation -> type=compute, target=x86-qemu
- If command asks to show/draw/display something -> type=draw
- If command is a general question, explanation, or conversation ("what is...", "explain...", "tell me...", and equivalents in any language) -> type=answer. NEVER draw or compute for questions.
- If command involves hardware (LED, sensor, temperature, GPIO, device) -> type=device
- Mobile edges (arch=mobile) can only receive images or text, never run assembly
- Default compute target: x86-qemu (i386)
- task should be a clear description for code/answer generation`,
    messages: [{ role: 'user', content: command }],
  })

  let text = msg.content[0].type === 'text' ? msg.content[0].text.trim() : '{}'
  text = text.replace(/^```\w*\n?/, '').replace(/\n?```$/, '').trim()
  try { return JSON.parse(text) }
  catch (e) {
    log.warn(`[planCommand] failed to parse LLM response: ${e.message}`)
    return { type: 'reply', target: fromId, task: command }
  }
}

// ── Generate text answer (no code execution) ──
async function generateAnswer(task) {
  const client = getClient()

  const msg = await client.messages.create({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 300,
    system: `You are the POKE hub assistant. Answer briefly in the same language as the question. Keep answers under 2 sentences.`,
    messages: [{ role: 'user', content: task }],
  })
  return msg.content[0].type === 'text' ? msg.content[0].text.trim() : ''
}

// ── Generate assembly (architecture-specific) ──
async function generateAssembly(task, arch) {
  const client = getClient()

  const isARM = arch === 'aarch64'
  const system = isARM
    ? `You generate ARM64 (AArch64) assembly code using GNU as syntax.

Rules:
- The code is called as a function (BL target). x0-x7 are scratch registers.
- Return the result in X0.
- End with RET.
- No directives like .global, .text, .section. Just flat instructions.
- No syscalls. Pure computation.
- Output ONLY the assembly code, no explanation.

Example:
  Task: "add 2 and 3"
  Output:
  mov x0, #2
  add x0, x0, #3
  ret`
    : `You generate x86 (i386, 32-bit) NASM assembly code.

Rules:
- The code will be injected into a running OS at an arbitrary address
- Use BITS 32
- The code is called as a function: it can use eax-edx freely
- Return the result in EAX
- End with RET
- No sections, no labels for entry. Just flat code.
- No syscalls. No interrupts. Pure computation.
- Output ONLY the assembly code, no explanation.

Example:
  Task: "add 2 and 3"
  Output:
  BITS 32
  mov eax, 2
  add eax, 3
  ret`

  const msg = await client.messages.create({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 500,
    system,
    messages: [{ role: 'user', content: task }],
  })

  const text = msg.content[0].type === 'text' ? msg.content[0].text.trim() : ''
  return text.replace(/^```\w*\n?/, '').replace(/\n?```$/, '').trim()
}

// ── Generate device code (profile-aware) ──
async function generateDeviceCode(task, arch, profileCtx) {
  const client = getClient()

  const isARM = arch === 'aarch64'
  const msg = await client.messages.create({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 1000,
    system: `You generate ${isARM ? 'ARM64 (AArch64) assembly (GNU as syntax)' : 'x86 (i386) NASM assembly'} for hardware device control.

Known devices on this system:
${profileCtx}

Rules:
${isARM ? '- Return result in X0, end with RET, no directives.' : '- BITS 32, return in EAX, end with RET.'}
- Use MMIO (memory-mapped I/O) or port I/O as appropriate for the device.
- For MMIO: read/write directly to memory addresses.
- For I/O ports (x86): use in/out with dx for port.
- Output ONLY assembly code, no explanation.`,
    messages: [{ role: 'user', content: task }],
  })

  const text = msg.content[0].type === 'text' ? msg.content[0].text.trim() : ''
  return text.replace(/^```\w*\n?/, '').replace(/\n?```$/, '').trim()
}

// ── Generate image code ──
async function generateImageCode(task) {
  const client = getClient()

  const msg = await client.messages.create({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 2000,
    system: `You generate Node.js code that creates pixel art images.

The code MUST:
1. Define W (width) and H (height), max 200x200
2. Create a Buffer: const pixels = Buffer.alloc(W * H * 3)
3. Define setPixel(x, y, r, g, b) that writes to pixels
4. Draw the requested image using setPixel, loops, and math
5. End with: module.exports = { W, H, pixels }

Helper functions you can define:
- fillCircle(cx, cy, radius, r, g, b)
- fillRect(x, y, w, h, r, g, b)
- fillEllipse(cx, cy, rx, ry, r, g, b)
- drawLine(x1, y1, x2, y2, r, g, b)

Use vibrant colors. Make it visually interesting.
Output ONLY the Node.js code, no explanation, no markdown.`,
    messages: [{ role: 'user', content: task }],
  })

  let text = msg.content[0].type === 'text' ? msg.content[0].text.trim() : ''
  return text.replace(/^```\w*\n?/, '').replace(/\n?```$/, '').trim()
}

module.exports = {
  planCommand,
  generateAnswer,
  generateAssembly,
  generateDeviceCode,
  generateImageCode,
}
