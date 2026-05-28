/**
 * POKE Hub — agent loop + tool definitions + tool execution
 */

const { log } = require('./logger')
const { nodes, profiles } = require('./nodes')
const { compileAssembly, compileAssemblyARM, runImageCode } = require('./compiler')
const { pokeNode, pokeNodeARM, pokeNodeRaw, pokeRelay, streamToEdge } = require('./transport')

// ── Base tools (always present) ──
const BASE_TOOLS = [
  {
    name: 'list_edges',
    description: 'List all connected edge devices with their architecture, capabilities, and status.',
    input_schema: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'list_devices',
    description: 'List all known hardware devices with their available operations.',
    input_schema: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'execute_x86',
    description: 'Compile and execute x86 (i386, NASM) assembly on a target edge. Returns eax value. MUST start with "BITS 32". Return in EAX. End with RET.',
    input_schema: {
      type: 'object',
      properties: { target: { type: 'string' }, asm_code: { type: 'string' } },
      required: ['target', 'asm_code'],
    },
  },
  {
    name: 'execute_arm',
    description: 'Compile and execute ARM64 (AArch64, GNU as) assembly on a target edge. Returns x0 value. Return in X0, end with RET. No directives.',
    input_schema: {
      type: 'object',
      properties: { target: { type: 'string' }, asm_code: { type: 'string' } },
      required: ['target', 'asm_code'],
    },
  },
  {
    name: 'draw_image',
    description: 'Generate pixel art image using Node.js code and send to a target edge. Code must define W, H, pixels Buffer, setPixel(x,y,r,g,b), and end with module.exports={W,H,pixels}.',
    input_schema: {
      type: 'object',
      properties: { target: { type: 'string' }, js_code: { type: 'string' } },
      required: ['target', 'js_code'],
    },
  },
  {
    name: 'build_and_deploy',
    description: `Build a C program and deploy it to an edge device. Use for complex tasks: monitoring, data collection, automation, multi-step hardware interaction, string processing, etc.

The C code runs in bare-metal (freestanding, no libc). Entry: void _start(char *buf, int *len).
Write results to buf, set *len = bytes written.

POKE SDK headers available (use #include):
- <poke/io.h>     — u8/u16/u32 types, inb/outb/inl/outl, mmio_read32/write32, poke_delay
- <poke/string.h> — poke_strlen, poke_memcpy, poke_memset, poke_strcmp, poke_strcpy, poke_strcat
- <poke/format.h> — poke_itoa(val,buf), poke_utoh(val,buf,digits), poke_btoh(byte,buf), poke_format_mac(mac6,buf), poke_format_ip(ip4,buf)
- <poke/pci.h>    — pci_read(bus,slot,func,off), pci_vendor/device/bar0/class(bus,slot), pci_scan(callback,ctx)
- <poke/net.h>    — e1000_t, e1000_init/read/write, e1000_link_up, e1000_read_mac(dev,mac6)

Example using SDK:
  #include <poke/net.h>
  #include <poke/format.h>
  void _start(char *buf, int *len) {
    e1000_t nic; e1000_init(&nic, 0xfebc0000);
    unsigned char mac[6]; e1000_read_mac(&nic, mac);
    *len = poke_format_mac(mac, buf);
  }`,
    input_schema: {
      type: 'object',
      properties: {
        target: { type: 'string', description: 'Edge node ID' },
        c_code: { type: 'string', description: 'C source code (freestanding, entry = _start(char*buf, int*len))' },
        description: { type: 'string', description: 'What this binary does' },
      },
      required: ['target', 'c_code'],
    },
  },
  {
    name: 'fetch_url',
    description: 'Fetch data from a URL (HTTP GET). Use for weather, APIs, external data. Returns text body. Example URLs: "wttr.in/Seoul?format=j1" for weather JSON, "wttr.in/Seoul?format=3" for one-line weather.',
    input_schema: {
      type: 'object',
      properties: { url: { type: 'string', description: 'URL to fetch' } },
      required: ['url'],
    },
  },
  {
    name: 'parallel_execute',
    description: `Execute the SAME assembly code on multiple edges simultaneously and collect all results.
Use this for embarrassingly parallel tasks: split a computation into N parts, run each on a different edge.
Returns an array of results from all edges.

Example: split "sum 1 to 1000000" across 2 edges:
  Edge 1 sums 1..500000, Edge 2 sums 500001..1000000, hub adds results.

The asm_code is the SAME for all targets. Use the 'params' array to pass different parameters to each edge.
Each param object has: target (edge ID), asm_code (x86 NASM or ARM64), arch (i386 or aarch64).`,
    input_schema: {
      type: 'object',
      properties: {
        tasks: {
          type: 'array',
          description: 'Array of {target, asm_code, arch} objects to execute in parallel',
          items: {
            type: 'object',
            properties: {
              target: { type: 'string' },
              asm_code: { type: 'string' },
              arch: { type: 'string', description: 'i386 or aarch64 (default: i386)' },
            },
            required: ['target', 'asm_code'],
          },
        },
      },
      required: ['tasks'],
    },
  },
  {
    name: 'check_edge_load',
    description: 'Check which edges are idle and available for work. Returns edge status with cpu_busy flag. Use this before parallel_execute to pick the best edges.',
    input_schema: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'peer_execute',
    description: `P2P: compile code and send from one edge to another. Hub acts as relay — compiles assembly, then sends the binary to the target edge. Use when one edge wants to trigger computation on another edge.`,
    input_schema: {
      type: 'object',
      properties: {
        from: { type: 'string', description: 'Source edge ID (who requested)' },
        to: { type: 'string', description: 'Target edge ID (who executes)' },
        asm_code: { type: 'string', description: 'Assembly code to compile and send' },
      },
      required: ['from', 'to', 'asm_code'],
    },
  },
  {
    name: 'stream_animation',
    description: `Stream a series of image frames to an edge display. Generates frames using Node.js code, then streams them as FRM packets. The JS code must export a function generateFrames(frameCount) that returns an array of {width, height, pixels: Buffer(w*h*3 RGB)}.`,
    input_schema: {
      type: 'object',
      properties: {
        target: { type: 'string', description: 'Edge to stream to' },
        js_code: { type: 'string', description: 'Node.js code that exports generateFrames(n)' },
        frame_count: { type: 'number', description: 'Number of frames (default 30)' },
        fps: { type: 'number', description: 'Frames per second (default 10)' },
      },
      required: ['target', 'js_code'],
    },
  },
  {
    name: 'reply_text',
    description: 'Send a text reply back to the user. Use this for questions, explanations, or status updates.',
    input_schema: {
      type: 'object',
      properties: { text: { type: 'string' } },
      required: ['text'],
    },
  },
]

// ── Auto-generated tools from profiles ──
function generateDeviceTools() {
  const tools = []
  for (const [id, profile] of profiles) {
    if (!profile.operations) continue
    for (const op of profile.operations) {
      const toolName = `${profile.type}_${op.name}`
      tools.push({
        name: toolName,
        description: `[${profile.name}] ${op.desc}. Returns: ${op.returns}`,
        input_schema: {
          type: 'object',
          properties: {
            target: { type: 'string', description: 'Edge node to execute on (default: x86-qemu)' },
          },
          required: [],
        },
        _profile: id,
        _asm: op.asm,
        _arch: profile.arch || 'i386',
      })
    }
  }
  return tools
}

function getAgentTools() {
  return [...BASE_TOOLS, ...generateDeviceTools()]
}

function getDeviceToolsSummary() {
  const devTools = generateDeviceTools()
  if (devTools.length === 0) return ''
  return '\nAuto-generated device tools (call directly, no assembly needed):\n' +
    devTools.map(t => `- ${t.name}: ${t.description}`).join('\n')
}

async function executeAgentTool(toolName, toolInput) {
  log.info(`[agent] tool: ${toolName}(${JSON.stringify(toolInput).slice(0, 100)})`)

  // ── Auto-generated device tools ──
  const devTools = generateDeviceTools()
  const devTool = devTools.find(t => t.name === toolName)
  if (devTool) {
    const target = toolInput.target || 'x86-qemu'
    const node = nodes.get(target)
    if (!node) return `Error: edge "${target}" not found`

    log.info(`[agent] device-op: ${toolName} on ${target} (${devTool._arch})`)

    try {
      if (devTool._arch === 'aarch64') {
        const bin = await compileAssemblyARM(devTool._asm)
        if (!bin) return 'Error: ARM compilation failed'
        return await pokeNodeARM(node.endpoint, bin)
      } else {
        const bin = await compileAssembly(devTool._asm)
        if (!bin) return 'Error: NASM compilation failed'
        return await pokeNode(node.endpoint, bin)
      }
    } catch (err) {
      return `Error: ${err.message}`
    }
  }

  // ── Base tools ──
  if (toolName === 'list_edges') {
    const edgeList = [...nodes.values()].map(n => ({
      node_id: n.node_id, arch: n.arch, endpoint: n.endpoint,
      status: n.status, capabilities: n.capabilities || [],
    }))
    return JSON.stringify(edgeList, null, 2)
  }

  if (toolName === 'list_devices') {
    const devList = [...profiles.values()].map(p => {
      const ops = (p.operations || []).map(o => `${p.type}_${o.name}`).join(', ')
      return `${p.vendor_device} ${p.name} [${p.type}] -> operations: ${ops || 'none'}`
    }).join('\n')
    return devList || 'No device profiles loaded.'
  }

  if (toolName === 'execute_x86') {
    const node = nodes.get(toolInput.target)
    if (!node) return `Error: edge "${toolInput.target}" not found`
    try {
      const bin = await compileAssembly(toolInput.asm_code)
      if (!bin) return 'Error: NASM compilation failed'
      return await pokeNode(node.endpoint, bin)
    } catch (err) {
      return `Error: ${err.message}`
    }
  }

  if (toolName === 'execute_arm') {
    const node = nodes.get(toolInput.target)
    if (!node) return `Error: edge "${toolInput.target}" not found`
    try {
      const bin = await compileAssemblyARM(toolInput.asm_code)
      if (!bin) return 'Error: ARM assembly compilation failed'
      return await pokeNodeARM(node.endpoint, bin)
    } catch (err) {
      return `Error: ${err.message}`
    }
  }

  if (toolName === 'draw_image') {
    const imgResult = runImageCode(toolInput.js_code)
    if (!imgResult) return 'Error: image generation failed'

    const node = nodes.get(toolInput.target)
    if (!node) return `Error: edge "${toolInput.target}" not found`

    if (node.endpoint.startsWith('polling:')) {
      node.pending_img = imgResult.body.slice(7)
      node.pending_w = imgResult.width
      node.pending_h = imgResult.height
      return `Image ${imgResult.width}x${imgResult.height} queued for ${toolInput.target}`
    } else {
      try {
        return await pokeNodeRaw(node.endpoint, imgResult.body)
      } catch (err) {
        return `Error: ${err.message}`
      }
    }
  }

  if (toolName === 'build_and_deploy') {
    const node = nodes.get(toolInput.target)
    if (!node) return `Error: edge "${toolInput.target}" not found`
    const arch = node.arch || 'i386'
    const fs = require('fs')
    const { execSync } = require('child_process')
    const path = require('path')

    const sdkInclude = path.join(__dirname, '..', 'sdk', 'include')
    const fullCode = toolInput.c_code
    const tmpC = '/tmp/poke_deploy.c'
    const tmpObj = '/tmp/poke_deploy.o'
    const tmpBin = '/tmp/poke_deploy.bin'

    fs.writeFileSync(tmpC, fullCode)

    try {
      if (arch === 'aarch64') {
        execSync(`aarch64-elf-gcc -I${sdkInclude} -ffreestanding -nostdlib -fno-builtin -O1 -c -o ${tmpObj} ${tmpC} 2>&1`)
        execSync(`aarch64-elf-objcopy -O binary -j .text ${tmpObj} ${tmpBin} 2>&1`)
      } else {
        execSync(`i686-elf-gcc -I${sdkInclude} -ffreestanding -nostdlib -fno-builtin -O1 -c -o ${tmpObj} ${tmpC} 2>&1`)
        execSync(`i686-elf-objcopy -O binary -j .text ${tmpObj} ${tmpBin} 2>&1`)
      }
    } catch (e) {
      const stderr = e.stderr ? e.stderr.toString() : e.message
      log.error('[build] compile error:', stderr)
      return 'Compile error: ' + stderr.slice(0, 500)
    }

    const bin = fs.readFileSync(tmpBin)
    const binSize = bin.length
    log.info(`[build] compiled ${binSize} bytes for ${arch}`)

    try {
      let result
      if (arch === 'aarch64') {
        result = await pokeNodeARM(node.endpoint, bin)
      } else {
        result = await pokeNode(node.endpoint, bin)
      }
      return `Binary deployed: ${binSize} bytes to ${toolInput.target} (${arch})\nResult: ${result}`
    } catch (err) {
      return `Error deploying: ${err.message}`
    }
  }

  if (toolName === 'fetch_url') {
    return new Promise((resolve) => {
      let fetchUrl = toolInput.url
      if (!fetchUrl.startsWith('http')) fetchUrl = 'http://' + fetchUrl
      const mod = fetchUrl.startsWith('https') ? require('https') : require('http')
      const fetchReq = mod.get(fetchUrl, { timeout: 10000, headers: { 'User-Agent': 'curl/7.0' } }, (res) => {
        const chunks = []
        let totalBytes = 0
        res.on('data', c => {
          totalBytes += c.length
          if (totalBytes <= 3000) chunks.push(c)
        })
        res.on('end', () => resolve(Buffer.concat(chunks).toString().slice(0, 3000)))
      })
      fetchReq.on('error', e => resolve('Fetch error: ' + e.message))
    })
  }

  if (toolName === 'parallel_execute') {
    const tasks = toolInput.tasks || []
    if (tasks.length === 0) return 'Error: no tasks provided'

    log.info(`[parallel] launching ${tasks.length} tasks across edges`)
    const startTime = Date.now()

    const promises = tasks.map(async (task, i) => {
      const node = nodes.get(task.target)
      if (!node) return { target: task.target, error: 'edge not found' }

      const arch = task.arch || node.arch || 'i386'
      const taskStart = Date.now()

      try {
        let result
        if (arch === 'aarch64') {
          const bin = await compileAssemblyARM(task.asm_code)
          if (!bin) return { target: task.target, error: 'ARM compilation failed' }
          result = await pokeNodeARM(node.endpoint, bin)
        } else {
          const bin = await compileAssembly(task.asm_code)
          if (!bin) return { target: task.target, error: 'x86 compilation failed' }
          result = await pokeNode(node.endpoint, bin)
        }

        return {
          target: task.target,
          arch,
          result,
          ms: Date.now() - taskStart,
        }
      } catch (err) {
        return { target: task.target, error: err.message, ms: Date.now() - taskStart }
      }
    })

    const results = await Promise.all(promises)
    const wallTime = Date.now() - startTime
    const successful = results.filter(r => !r.error)
    const failed = results.filter(r => r.error)

    log.info(`[parallel] done: ${successful.length}/${tasks.length} succeeded in ${wallTime}ms`)

    return JSON.stringify({
      total: tasks.length,
      succeeded: successful.length,
      failed: failed.length,
      wall_time_ms: wallTime,
      results,
    }, null, 2)
  }

  if (toolName === 'check_edge_load') {
    const edgeStatus = []
    for (const [id, node] of nodes) {
      if (node.endpoint.startsWith('polling:')) continue  // skip mobile
      edgeStatus.push({
        node_id: id,
        arch: node.arch,
        status: node.status,
        cpu_busy: node.health?.cpu_busy || false,
        last_seen: node.last_seen,
        available: node.status === 'alive' && !node.health?.cpu_busy,
      })
    }
    return JSON.stringify(edgeStatus, null, 2)
  }

  if (toolName === 'peer_execute') {
    const fromNode = nodes.get(toolInput.from)
    const toNode = nodes.get(toolInput.to)
    if (!fromNode) return `Error: source edge "${toolInput.from}" not found`
    if (!toNode) return `Error: target edge "${toolInput.to}" not found`

    const arch = toNode.arch || 'i386'
    try {
      let bin
      if (arch === 'aarch64') {
        bin = await compileAssemblyARM(toolInput.asm_code)
      } else {
        bin = await compileAssembly(toolInput.asm_code)
      }
      if (!bin) return 'Error: compilation failed'

      const result = await pokeRelay(fromNode.endpoint, toNode.endpoint, bin, arch)
      return `P2P ${toolInput.from} → ${toolInput.to}: ${result}`
    } catch (err) {
      return `P2P error: ${err.message}`
    }
  }

  if (toolName === 'stream_animation') {
    const node = nodes.get(toolInput.target)
    if (!node) return `Error: edge "${toolInput.target}" not found`

    const fs = require('fs')
    const { execSync } = require('child_process')
    const frameCount = toolInput.frame_count || 30
    const fps = toolInput.fps || 10

    // Generate frames using Node.js code
    const tmpFile = '/tmp/poke_stream_gen.js'
    const wrapped = toolInput.js_code + `
;(function() {
  const frames = generateFrames(${frameCount});
  const out = frames.map(f => ({
    width: f.width, height: f.height,
    pixels: f.pixels.toString('base64')
  }));
  process.stdout.write(JSON.stringify(out));
})();`

    fs.writeFileSync(tmpFile, wrapped)

    try {
      const result = execSync(`node ${tmpFile}`, { timeout: 10000, maxBuffer: 50 * 1024 * 1024 })
      const framesData = JSON.parse(result.toString())

      const frames = framesData.map(f => ({
        width: f.width,
        height: f.height,
        pixels: Buffer.from(f.pixels, 'base64'),
      }))

      log.info(`[stream] generated ${frames.length} frames, streaming at ${fps}fps`)

      const streamResult = await streamToEdge(node.endpoint, frames, fps)
      return JSON.stringify(streamResult)
    } catch (err) {
      return `Stream error: ${err.message}`
    }
  }

  if (toolName === 'reply_text') {
    return toolInput.text
  }

  return `Unknown tool: ${toolName}`
}

async function agentLoop(command, fromId, targetHint) {
  const Anthropic = require('@anthropic-ai/sdk')
  const client = new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY })

  const edgeList = [...nodes.values()].map(n =>
    `${n.node_id}: arch=${n.arch}, endpoint=${n.endpoint}, caps=${(n.capabilities||[]).join(',')}`
  ).join('\n')

  const systemPrompt = `You are the POKE hub agent. You control edge devices by generating and executing machine code.

Connected edges:
${edgeList}

${targetHint ? `User specified target: ${targetHint}` : ''}

You have tools to:
- List edges and devices
- Execute raw x86 (NASM) or ARM64 (GNU as) assembly on edges
- Call pre-built device operations directly (no assembly needed!)
- Generate and send images to edges
- Reply with text
${getDeviceToolsSummary()}

Rules:
- For math/computation: use execute_x86 or execute_arm based on target architecture.
- x86 assembly: MUST start with "BITS 32" on first line. Return result in EAX. End with RET. No sections, no labels. Example: "BITS 32\\nmov eax, 2\\nadd eax, 3\\nret"
- ARM64 assembly: return in X0, end with RET. No .global, .text directives. Just instructions.
- If code fails (compile error, wrong result), read the error, fix the code, and retry.
- For questions/conversation: use reply_text.
- For images: use draw_image with Node.js code.
- For device interaction: check profiles first with list_devices, then call auto-generated tools.
- Keep it concise. Finish as quickly as possible.
- Respond in the same language as the user's command.
- Max 7 tool calls per request.

Distributed computing rules:
- For large computations, use check_edge_load first to find available edges.
- Split the task into independent parts and use parallel_execute to run them simultaneously.
- Example: "sum 1 to 1 billion" with 2 edges → edge1 sums 1..500M, edge2 sums 500M+1..1B → add results.
- Each parallel task gets its own assembly with different parameters (ranges, offsets, etc.).
- After parallel_execute, combine the partial results (sum, max, min, average, etc.) and reply.
- ALWAYS prefer parallel_execute over sequential execute_x86 calls when multiple edges are available and the task is decomposable.

P2P rules:
- Use peer_execute when one edge needs to trigger computation on another edge.
- Hub compiles the code and relays the binary — edges don't need to compile.

Streaming rules:
- Use stream_animation to send animated frames to an edge display.
- The JS code must export generateFrames(n) returning [{width, height, pixels: Buffer}].`

  const messages = [{ role: 'user', content: command }]
  const steps = []
  let finalResult = null
  const MAX_TURNS = 7

  for (let turn = 0; turn < MAX_TURNS; turn++) {
    const response = await client.messages.create({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 1500,
      system: systemPrompt,
      tools: getAgentTools().map(t => ({
        name: t.name, description: t.description, input_schema: t.input_schema,
      })),
      messages,
    })

    const toolUses = response.content.filter(b => b.type === 'tool_use')
    const textBlocks = response.content.filter(b => b.type === 'text')

    if (textBlocks.length > 0) {
      finalResult = textBlocks.map(b => b.text).join('\n')
    }

    if (toolUses.length === 0) {
      log.info(`[agent] done in ${turn + 1} turns`)
      break
    }

    messages.push({ role: 'assistant', content: response.content })
    const toolResults = []

    for (const tu of toolUses) {
      const result = await executeAgentTool(tu.name, tu.input)
      const resultStr = typeof result === 'string' ? result : JSON.stringify(result)
      log.debug(`[agent] ${tu.name} -> ${resultStr.slice(0, 80)}`)

      steps.push({ tool: tu.name, input: tu.input, result: resultStr })
      toolResults.push({ type: 'tool_result', tool_use_id: tu.id, content: resultStr })

      if (tu.name === 'reply_text') {
        finalResult = tu.input.text
      }
    }

    messages.push({ role: 'user', content: toolResults })

    if (response.stop_reason === 'end_turn') {
      log.info(`[agent] end_turn at turn ${turn + 1}`)
      break
    }
  }

  return { steps, result: finalResult }
}

module.exports = {
  agentLoop,
  executeAgentTool,
  getAgentTools,
  BASE_TOOLS,
}
