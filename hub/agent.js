/**
 * POKE Hub — agent loop + tool definitions + tool execution
 */

const { log } = require('./logger')
const { nodes, profiles, loadProfiles } = require('./nodes')
const { compileAssembly, compileAssemblyARM, runImageCode } = require('./compiler')
const { pokeNode, pokeNodeARM, pokeNodeRaw, pokeRelay, streamToEdge } = require('./transport')
const memory = require('./memory')
const trace = require('./trace')

// ── Active monitors (Task 2: Autonomous Agent) ──
const activeMonitors = new Map()

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
    name: 'execute_rv',
    description: 'Compile and execute RISC-V (RV32IM) assembly on a target edge (ESP32-C3). Returns a0 value. Return in a0, end with ret. Use standard RISC-V register names (a0-a7, s0-s11, t0-t6, sp, ra, zero).',
    input_schema: {
      type: 'object',
      properties: { target: { type: 'string' }, asm_code: { type: 'string' } },
      required: ['target', 'asm_code'],
    },
  },
  {
    name: 'execute_armv6',
    description: 'Compile and execute ARMv6 (ARM 32-bit, ARM mode, GNU as) assembly on a target edge (Pi Zero W, BCM2835). Returns r0 value. Return in R0, end with BX LR. Registers: r0-r12, sp, lr, pc. Use ldr Rd, =value for 32-bit constants (e.g., register addresses).',
    input_schema: {
      type: 'object',
      properties: { target: { type: 'string' }, asm_code: { type: 'string' } },
      required: ['target', 'asm_code'],
    },
  },
  {
    name: 'read_sensor',
    description: 'Read a calibrated sensor value from a serial edge. Supported sensors: "temp" (internal temperature in celsius). This uses the firmware\'s built-in driver with proper calibration — prefer this over raw register access via execute_rv for sensor readings.',
    input_schema: {
      type: 'object',
      properties: {
        target: { type: 'string', description: 'Edge node ID' },
        sensor: { type: 'string', enum: ['temp'], description: 'Sensor type to read' },
      },
      required: ['target', 'sensor'],
    },
  },
  {
    name: 'deploy_serial_monitor',
    description: `Deploy an autonomous event monitor on a serial edge (ESP32-C3). The edge will watch for a condition and fire an EVNT frame back to the hub when triggered — no polling needed.

Monitor types:
- gpio: Watch a GPIO pin for state change. Params: pin (0-21), edge ("falling"=0, "rising"=1, "both"=2). ESP32-C3 has a built-in BOOT button on GPIO 9.
- temp: Watch internal temperature threshold. Params: op ("above"=0, "below"=1), threshold (celsius), interval_ms (min 1000).

When the condition triggers, the edge sends an EVNT frame. The hub automatically runs agentLoop to decide what to do.`,
    input_schema: {
      type: 'object',
      properties: {
        target: { type: 'string', description: 'Edge node ID' },
        monitor_type: { type: 'string', enum: ['gpio', 'temp'], description: 'What to monitor' },
        pin: { type: 'number', description: 'GPIO pin number (for gpio type)' },
        edge_trigger: { type: 'number', enum: [0, 1, 2], description: '0=falling, 1=rising, 2=both (for gpio type)' },
        op: { type: 'number', enum: [0, 1], description: '0=above threshold, 1=below threshold (for temp type)' },
        threshold: { type: 'number', description: 'Temperature threshold in celsius (for temp type)' },
        interval_ms: { type: 'number', description: 'Check interval in ms, min 1000 (for temp type)' },
      },
      required: ['target', 'monitor_type'],
    },
  },
  {
    name: 'stop_serial_monitor',
    description: 'Stop all active event monitors on a serial edge.',
    input_schema: {
      type: 'object',
      properties: { target: { type: 'string', description: 'Edge node ID' } },
      required: ['target'],
    },
  },
  {
    name: 'set_gpio',
    description: 'Set a GPIO pin output on a serial edge. Use for controlling LEDs, relays, motors, etc.',
    input_schema: {
      type: 'object',
      properties: {
        target: { type: 'string', description: 'Edge node ID' },
        pin: { type: 'number', description: 'GPIO pin number (0-21)' },
        value: { type: 'number', enum: [0, 1], description: '0=LOW, 1=HIGH' },
      },
      required: ['target', 'pin', 'value'],
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
  // ── Task 1: Multimodal — image input → LLM analysis → hardware reaction ──
  {
    name: 'analyze_image',
    description: 'Analyze an image using Claude vision API. Accepts an image URL or base64-encoded image data with an instruction. Returns analysis text describing the image content and suggested hardware actions.',
    input_schema: {
      type: 'object',
      properties: {
        image_url: { type: 'string', description: 'URL of the image to analyze' },
        image_base64: { type: 'string', description: 'Base64-encoded image data (PNG/JPEG)' },
        instruction: { type: 'string', description: 'What to analyze or do with the image (default: describe and suggest hardware action)' },
        target: { type: 'string', description: 'Edge node to act on based on analysis' },
      },
      required: [],
    },
  },
  // ── Task 2: Autonomous Agent — deploy condition-checking monitors ──
  {
    name: 'deploy_autonomous',
    description: `Deploy an autonomous monitor on an edge. Generates a C binary that checks a hardware condition. The hub periodically re-executes the binary and reports results. Returns monitor ID.`,
    input_schema: {
      type: 'object',
      properties: {
        target: { type: 'string', description: 'Edge node ID to monitor' },
        condition: { type: 'string', description: 'Condition to check (e.g. "temperature above 80C")' },
        action: { type: 'string', description: 'Action description when condition is met (e.g. "alert user")' },
        check_interval_ms: { type: 'number', description: 'Check interval in milliseconds (default: 5000)' },
      },
      required: ['target', 'condition', 'action'],
    },
  },
  {
    name: 'list_monitors',
    description: 'List all active autonomous monitors with their status and last check results.',
    input_schema: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'stop_monitor',
    description: 'Stop an active autonomous monitor by its ID.',
    input_schema: {
      type: 'object',
      properties: {
        monitor_id: { type: 'string', description: 'Monitor ID to stop' },
      },
      required: ['monitor_id'],
    },
  },
  // ── Task 3: Auto Profile Generation ──
  {
    name: 'auto_profile',
    description: `Automatically scan the PCI bus on a target edge, probe each discovered device, generate device profiles with operations, save them to profiles/ directory, and reload so new tools are immediately available.`,
    input_schema: {
      type: 'object',
      properties: {
        target: { type: 'string', description: 'Edge node ID to scan (default: x86-qemu)' },
      },
      required: [],
    },
  },
  // ── Autonomous monitor (edge-initiated events) ──
  {
    name: 'deploy_monitor',
    description: `Deploy a condition monitor on a bare-metal edge. The edge autonomously checks a condition by running assembly code periodically. When the condition is met, the edge fires an HTTP POST to the hub, which re-enters the agent loop.

Unlike deploy_autonomous (hub polls edge), this is edge-initiated: the edge fires events on its own. Zero hub overhead.

The assembly code must return a sensor/computed value in EAX. The condition compares EAX against a threshold.`,
    input_schema: {
      type: 'object',
      properties: {
        target: { type: 'string', description: 'Edge node ID' },
        asm_code: { type: 'string', description: 'Assembly code that returns a value in EAX (e.g., read sensor)' },
        condition: { type: 'string', description: 'Condition: "gt:30", "lt:10", "eq:0", "ne:0"' },
        interval_ms: { type: 'number', description: 'Check interval in milliseconds (default: 1000)' },
      },
      required: ['target', 'asm_code', 'condition'],
    },
  },
  // ── Factory line control (actual QEMU shutdown/restart) ──
  {
    name: 'shutdown_line',
    description: 'ACTUALLY shut down a production line. This kills the QEMU process — the edge goes offline. Use only for low-priority lines in emergencies. NEVER shut down critical lines (web, db).',
    input_schema: {
      type: 'object',
      properties: {
        target: { type: 'string', description: 'Edge node ID to shut down (e.g. "line-C")' },
        reason: { type: 'string', description: 'Reason for shutdown' },
      },
      required: ['target', 'reason'],
    },
  },
  {
    name: 'restart_line',
    description: 'Restart a previously shut down production line. Takes ~10 seconds to boot. Use after conditions normalize.',
    input_schema: {
      type: 'object',
      properties: {
        target: { type: 'string', description: 'Edge node ID to restart (e.g. "line-C")' },
      },
      required: ['target'],
    },
  },
  // ── Resident binary (persistent control loops) ──
  {
    name: 'deploy_resident',
    description: `Deploy a PERSISTENT binary on an edge that runs continuously. Use for: PID control, data logging, safety monitoring, continuous sensor polling. Runs every N ticks without hub involvement. Survives edge reboot. Up to 4 slots (0-3). Assembly returns status in EAX.`,
    input_schema: {
      type: 'object',
      properties: {
        target: { type: 'string', description: 'Edge node ID' },
        slot: { type: 'number', description: 'Slot 0-3' },
        asm_code: { type: 'string', description: 'x86 assembly (runs in loop, return status in EAX)' },
        interval: { type: 'number', description: 'Ticks between calls. 0=every tick, 1000=~1sec' },
      },
      required: ['target', 'slot', 'asm_code'],
    },
  },
  {
    name: 'stop_resident',
    description: 'Stop and remove a resident binary from a slot.',
    input_schema: {
      type: 'object',
      properties: {
        target: { type: 'string', description: 'Edge node ID' },
        slot: { type: 'number', description: 'Slot 0-3' },
      },
      required: ['target', 'slot'],
    },
  },
  // ── Assembly cache tools ──
  {
    name: 'asm_cache_save',
    description: 'Save a reusable assembly template. Use after you create assembly that works well — save it for reuse. Template uses {{PARAM}} placeholders. Example: key="read_temp", template="BITS 32\\nmov ebx, {{BAR0}}\\nmov eax, [ebx + 0x10]\\nret", params=["BAR0"]',
    input_schema: {
      type: 'object',
      properties: {
        key: { type: 'string', description: 'Unique key for this template' },
        template: { type: 'string', description: 'Assembly with {{PARAM}} placeholders' },
        params: { type: 'array', items: { type: 'string' }, description: 'Parameter names' },
        desc: { type: 'string', description: 'What this code does' },
        tags: { type: 'array', items: { type: 'string' }, description: 'Tags for search' },
      },
      required: ['key', 'template', 'params'],
    },
  },
  {
    name: 'asm_cache_run',
    description: 'Execute a cached assembly template with parameters. Much faster than generating new assembly — uses pre-compiled cache. List available templates with asm_cache_list first.',
    input_schema: {
      type: 'object',
      properties: {
        key: { type: 'string', description: 'Template key to execute' },
        target: { type: 'string', description: 'Edge node ID' },
        params: { type: 'object', description: 'Parameter values: {"BAR0": "0xFEB80000"}' },
      },
      required: ['key', 'target'],
    },
  },
  {
    name: 'asm_cache_list',
    description: 'List all cached assembly templates with their keys, parameters, and usage counts.',
    input_schema: { type: 'object', properties: {}, required: [] },
  },
  // ── Memory tools (JARVIS-style persistent memory) ──
  {
    name: 'memory_save',
    description: 'Save an important fact to persistent memory. Use this when the user tells you something worth remembering (preferences, names, project context, device nicknames, etc.).',
    input_schema: {
      type: 'object',
      properties: {
        fact: { type: 'string', description: 'The fact to remember' },
        category: { type: 'string', description: 'Category: preference, context, device, schedule, or general' },
      },
      required: ['fact'],
    },
  },
  {
    name: 'memory_search',
    description: 'Search past conversation history. Use when user asks about previous commands, results, or says "last time", "before", "remember when".',
    input_schema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Search keyword or phrase' },
      },
      required: ['query'],
    },
  },
  {
    name: 'memory_stats',
    description: 'Show memory statistics: total conversations, facts, usage patterns, most used edges, top keywords.',
    input_schema: { type: 'object', properties: {}, required: [] },
  },
]

// ── Auto-generated tools from profiles ──
function generateDeviceTools() {
  const tools = []
  const usedNames = new Set()
  for (const [id, profile] of profiles) {
    if (!profile.operations) continue
    for (const op of profile.operations) {
      let toolName = `${profile.type}_${op.name}`
      // Deduplicate: append vendor_device suffix if name already used
      if (usedNames.has(toolName)) {
        const suffix = id.replace(/[^a-zA-Z0-9]/g, '_').toLowerCase()
        toolName = `${toolName}_${suffix}`
      }
      usedNames.add(toolName)
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

// ── Library tools (from device library, matched per edge) ──
let _cachedLibraryTools = []

function refreshLibraryTools() {
  const { entries, matchEdge, generateLibraryTools } = require('./library')
  const tools = []
  for (const [nodeId, node] of nodes) {
    // Use cached health info for matching
    const info = node.health || { chip: node._chip, sensors: node._sensors }
    const matched = matchEdge(node, info)
    if (matched.length > 0) {
      tools.push(...generateLibraryTools(nodeId, matched))
      log.debug(`[library] ${nodeId}: matched ${matched.length} entries, ${tools.length} tools`)
    }
  }
  _cachedLibraryTools = tools
  return tools
}

function getLibraryToolsForAgent() {
  return _cachedLibraryTools
}

function getAgentTools() {
  return [...BASE_TOOLS, ...generateDeviceTools(), ...getLibraryToolsForAgent()]
}

function getDeviceToolsSummary() {
  const devTools = generateDeviceTools()
  const libTools = getLibraryToolsForAgent()
  const all = [...devTools, ...libTools]
  if (all.length === 0) return ''
  return '\nAuto-generated device tools (call directly, no assembly needed):\n' +
    all.map(t => `- ${t.name}: ${t.description}`).join('\n')
}

async function executeAgentTool(toolName, toolInput) {
  log.info(`[agent] tool: ${toolName}(${JSON.stringify(toolInput).slice(0, 100)})`)

  // ── Library tools (matched from device library) ──
  const libTools = getLibraryToolsForAgent()
  const libTool = libTools.find(t => t.name === toolName)
  if (libTool) {
    log.info(`[agent] library-op: ${toolName} on ${toolInput.target || libTool._defaultTarget}`)
    try {
      const { executeLibraryTool } = require('./library')
      return await executeLibraryTool(libTool, toolInput)
    } catch (err) {
      return `Error: ${err.message}`
    }
  }

  // ── Auto-generated device tools (legacy profiles) ──
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

  if (toolName === 'execute_rv') {
    const node = nodes.get(toolInput.target)
    if (!node) return `Error: edge "${toolInput.target}" not found`
    try {
      const { compileAssemblyRV } = require('./compiler')
      const bin = await compileAssemblyRV(toolInput.asm_code)
      if (!bin) return 'Error: RISC-V assembly compilation failed'
      if (node.endpoint.startsWith('serial://') || node.endpoint.startsWith('tcp://')) {
        const { pokeNodeSerial } = require('./serial')
        return await pokeNodeSerial(node.endpoint, bin)
      }
      return await pokeNode(node.endpoint, bin)
    } catch (err) {
      return `Error: ${err.message}`
    }
  }

  if (toolName === 'execute_armv6') {
    const node = nodes.get(toolInput.target)
    if (!node) return `Error: edge "${toolInput.target}" not found`
    try {
      const { compileAssemblyARMv6 } = require('./compiler')
      const bin = await compileAssemblyARMv6(toolInput.asm_code)
      if (!bin) return 'Error: ARMv6 assembly compilation failed'
      if (node.endpoint.startsWith('serial://') || node.endpoint.startsWith('tcp://')) {
        const { pokeNodeSerial } = require('./serial')
        return await pokeNodeSerial(node.endpoint, bin)
      }
      return await pokeNode(node.endpoint, bin)
    } catch (err) {
      return `Error: ${err.message}`
    }
  }

  if (toolName === 'deploy_serial_monitor') {
    const node = nodes.get(toolInput.target)
    if (!node) return `Error: edge "${toolInput.target}" not found`
    if (!node.endpoint.startsWith('serial://') && !node.endpoint.startsWith('tcp://')) return 'Error: only works with serial/tcp edges'
    try {
      const serial = require('./serial')
      let config
      if (toolInput.monitor_type === 'gpio') {
        const pin = toolInput.pin ?? 9
        const edge = toolInput.edge_trigger ?? 0
        config = Buffer.from([0x01, pin, edge])
      } else if (toolInput.monitor_type === 'temp') {
        const op = toolInput.op ?? 0
        const threshX10 = Math.round((toolInput.threshold ?? 40) * 10)
        const interval = toolInput.interval_ms ?? 5000
        config = Buffer.alloc(6)
        config[0] = 0x02
        config[1] = op
        config.writeUInt16LE(threshX10, 2)
        config.writeUInt16LE(interval, 4)
      } else {
        return `Error: unknown monitor type "${toolInput.monitor_type}"`
      }
      return await serial.serialEventMonitor(node.endpoint, config)
    } catch (err) {
      return `Error: ${err.message}`
    }
  }

  if (toolName === 'stop_serial_monitor') {
    const node = nodes.get(toolInput.target)
    if (!node) return `Error: edge "${toolInput.target}" not found`
    if (!node.endpoint.startsWith('serial://') && !node.endpoint.startsWith('tcp://')) return 'Error: only works with serial/tcp edges'
    try {
      const serial = require('./serial')
      return await serial.serialEventStop(node.endpoint)
    } catch (err) {
      return `Error: ${err.message}`
    }
  }

  if (toolName === 'set_gpio') {
    const node = nodes.get(toolInput.target)
    if (!node) return `Error: edge "${toolInput.target}" not found`
    if (!node.endpoint.startsWith('serial://') && !node.endpoint.startsWith('tcp://')) return 'Error: only works with serial/tcp edges'
    try {
      const serial = require('./serial')
      return await serial.serialGpioSet(node.endpoint, toolInput.pin, toolInput.value)
    } catch (err) {
      return `Error: ${err.message}`
    }
  }

  if (toolName === 'read_sensor') {
    const node = nodes.get(toolInput.target)
    if (!node) return `Error: edge "${toolInput.target}" not found`
    if (!node.endpoint.startsWith('serial://') && !node.endpoint.startsWith('tcp://')) return 'Error: read_sensor only works with serial/tcp edges'
    try {
      const serial = require('./serial')
      if (toolInput.sensor === 'temp') {
        const data = await serial.serialTemp(node.endpoint)
        return data  // JSON: {"celsius":38.1,"raw_api":true}
      }
      return `Error: unknown sensor "${toolInput.sensor}"`
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

  // ── Task 1: analyze_image ──
  if (toolName === 'analyze_image') {
    try {
      const Anthropic = require('@anthropic-ai/sdk')
      const client = new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY })

      let imageContent = null

      if (toolInput.image_base64) {
        // Direct base64 data
        const mediaType = detectMediaType(toolInput.image_base64)
        imageContent = {
          type: 'image',
          source: {
            type: 'base64',
            media_type: mediaType,
            data: toolInput.image_base64,
          },
        }
      } else if (toolInput.image_url) {
        // Fetch image from URL
        const imageData = await fetchImageAsBase64(toolInput.image_url)
        if (!imageData) return 'Error: failed to fetch image from URL'
        imageContent = {
          type: 'image',
          source: {
            type: 'base64',
            media_type: imageData.mediaType,
            data: imageData.data,
          },
        }
      } else {
        return 'Error: provide either image_url or image_base64'
      }

      const instruction = toolInput.instruction || 'Describe this image and suggest what hardware action a POKE edge device should take based on what you see. Be specific about registers, I/O ports, or display actions.'

      const msg = await client.messages.create({
        model: 'claude-haiku-4-5-20251001',
        max_tokens: 1000,
        messages: [{
          role: 'user',
          content: [
            imageContent,
            { type: 'text', text: instruction },
          ],
        }],
      })

      const analysis = msg.content[0].type === 'text' ? msg.content[0].text.trim() : 'No analysis generated'
      log.info(`[analyze_image] analysis: ${analysis.slice(0, 100)}...`)
      return `Image analysis:\n${analysis}`
    } catch (err) {
      log.error(`[analyze_image] error: ${err.message}`)
      return `Error analyzing image: ${err.message}`
    }
  }

  // ── Task 2: deploy_autonomous ──
  if (toolName === 'deploy_autonomous') {
    const target = toolInput.target
    const node = nodes.get(target)
    if (!node) return `Error: edge "${target}" not found`

    const interval = toolInput.check_interval_ms || 5000
    const monitorId = `mon_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`

    // Generate monitoring C code using LLM
    const Anthropic = require('@anthropic-ai/sdk')
    const client = new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY })
    const arch = node.arch || 'i386'

    const msg = await client.messages.create({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 1500,
      system: `You generate bare-metal C code for the POKE system. The code runs in freestanding mode (no libc).
Entry: void _start(char *buf, int *len). Write results to buf, set *len = bytes written.

Available SDK headers:
- <poke/io.h> — u8/u16/u32, inb/outb/inl/outl, mmio_read32/write32, poke_delay
- <poke/string.h> — poke_strlen, poke_memcpy, poke_memset, poke_strcmp, poke_strcpy, poke_strcat
- <poke/format.h> — poke_itoa, poke_utoh, poke_btoh
- <poke/pci.h> — pci_read, pci_vendor, pci_device, pci_bar0, pci_class, pci_scan

Generate a monitoring check that:
1. Reads the hardware state relevant to the condition
2. Writes "CONDITION_MET:1" or "CONDITION_MET:0" to buf
3. Optionally appends sensor data after a newline

Output ONLY C code, no explanation.`,
      messages: [{ role: 'user', content: `Condition to check: "${toolInput.condition}"\nAction when met: "${toolInput.action}"\nTarget architecture: ${arch}` }],
    })

    const cCode = msg.content[0].type === 'text'
      ? msg.content[0].text.trim().replace(/^```\w*\n?/, '').replace(/\n?```$/, '').trim()
      : null

    if (!cCode) return 'Error: LLM failed to generate monitoring code'

    log.info(`[deploy_autonomous] generated ${cCode.length} chars of C for monitor ${monitorId}`)

    // Compile the code
    const fs = require('fs')
    const { execSync } = require('child_process')
    const path = require('path')
    const sdkInclude = path.join(__dirname, '..', 'sdk', 'include')
    const tmpC = `/tmp/poke_monitor_${monitorId}.c`
    const tmpObj = `/tmp/poke_monitor_${monitorId}.o`
    const tmpBin = `/tmp/poke_monitor_${monitorId}.bin`

    fs.writeFileSync(tmpC, cCode)

    let bin
    try {
      if (arch === 'aarch64') {
        execSync(`aarch64-elf-gcc -I${sdkInclude} -ffreestanding -nostdlib -fno-builtin -O1 -c -o ${tmpObj} ${tmpC} 2>&1`)
        execSync(`aarch64-elf-objcopy -O binary -j .text ${tmpObj} ${tmpBin} 2>&1`)
      } else {
        execSync(`i686-elf-gcc -I${sdkInclude} -ffreestanding -nostdlib -fno-builtin -O1 -c -o ${tmpObj} ${tmpC} 2>&1`)
        execSync(`i686-elf-objcopy -O binary -j .text ${tmpObj} ${tmpBin} 2>&1`)
      }
      bin = fs.readFileSync(tmpBin)
    } catch (e) {
      const stderr = e.stderr ? e.stderr.toString() : e.message
      log.error(`[deploy_autonomous] compile error: ${stderr}`)
      return `Error compiling monitor: ${stderr.slice(0, 500)}`
    }

    // Set up periodic re-execution
    const monitor = {
      id: monitorId,
      target,
      condition: toolInput.condition,
      action: toolInput.action,
      interval,
      binary: bin,
      arch,
      created_at: new Date().toISOString(),
      last_check: null,
      last_result: null,
      check_count: 0,
      condition_met_count: 0,
      timer: null,
    }

    // Deploy and check function
    const checkFn = async () => {
      try {
        let result
        if (arch === 'aarch64') {
          result = await pokeNodeARM(node.endpoint, bin)
        } else {
          result = await pokeNode(node.endpoint, bin)
        }
        monitor.last_check = new Date().toISOString()
        monitor.last_result = result
        monitor.check_count++

        if (result && result.includes('CONDITION_MET:1')) {
          monitor.condition_met_count++
          log.info(`[monitor:${monitorId}] CONDITION MET (${monitor.condition_met_count}x): ${toolInput.condition}`)
        }
      } catch (err) {
        monitor.last_check = new Date().toISOString()
        monitor.last_result = `Error: ${err.message}`
        log.warn(`[monitor:${monitorId}] check failed: ${err.message}`)
      }
    }

    // Run initial check
    await checkFn()

    // Set up periodic re-execution
    monitor.timer = setInterval(checkFn, interval)
    activeMonitors.set(monitorId, monitor)

    log.info(`[deploy_autonomous] monitor ${monitorId} deployed, checking every ${interval}ms`)
    return `Monitor deployed: ${monitorId}\nTarget: ${target}\nCondition: ${toolInput.condition}\nAction: ${toolInput.action}\nInterval: ${interval}ms\nBinary: ${bin.length} bytes\nInitial result: ${monitor.last_result}`
  }

  if (toolName === 'list_monitors') {
    if (activeMonitors.size === 0) return 'No active monitors.'
    const list = []
    for (const [id, mon] of activeMonitors) {
      list.push({
        id,
        target: mon.target,
        condition: mon.condition,
        action: mon.action,
        interval_ms: mon.interval,
        created_at: mon.created_at,
        last_check: mon.last_check,
        last_result: mon.last_result,
        check_count: mon.check_count,
        condition_met_count: mon.condition_met_count,
      })
    }
    return JSON.stringify(list, null, 2)
  }

  if (toolName === 'stop_monitor') {
    const monitorId = toolInput.monitor_id
    const monitor = activeMonitors.get(monitorId)
    if (!monitor) return `Error: monitor "${monitorId}" not found`
    if (monitor.timer) clearInterval(monitor.timer)
    activeMonitors.delete(monitorId)
    log.info(`[monitor] stopped: ${monitorId}`)
    return `Monitor ${monitorId} stopped. Was checking: "${monitor.condition}" (ran ${monitor.check_count} checks, condition met ${monitor.condition_met_count} times)`
  }

  // ── Task 3: auto_profile ──
  if (toolName === 'auto_profile') {
    const target = toolInput.target || 'x86-qemu'
    const node = nodes.get(target)
    if (!node) return `Error: edge "${target}" not found`
    const arch = node.arch || 'i386'

    if (arch !== 'i386') return 'Error: auto_profile currently supports x86 (i386) edges only (PCI config space uses I/O ports 0xCF8/0xCFC)'

    const Anthropic = require('@anthropic-ai/sdk')
    const client = new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY })
    const fs = require('fs')
    const path = require('path')

    const PCI_VENDORS = {
      '8086': 'Intel', '1234': 'QEMU', '1AF4': 'Red Hat (VirtIO)',
      '10EC': 'Realtek', '14E4': 'Broadcom', '10DE': 'NVIDIA', '1002': 'AMD/ATI',
    }
    const PCI_DEVICES = {
      '8086:100E': { name: 'Intel 82540EM (e1000)', type: 'network' },
      '8086:2415': { name: 'Intel AC97 Audio', type: 'audio' },
      '1234:1111': { name: 'QEMU stdvga', type: 'graphics' },
      '1AF4:1000': { name: 'VirtIO Network', type: 'network' },
      '1AF4:1005': { name: 'VirtIO RNG', type: 'rng' },
    }

    log.info(`[auto_profile] scanning PCI bus on ${target}...`)
    const results = []

    // Scan all 32 PCI slots
    const discoveredDevices = []
    for (let slot = 0; slot < 32; slot++) {
      const addr = 0x80000000 | (slot << 11)
      const asmCode = `BITS 32\nmov eax, ${addr}\nmov dx, 0xCF8\nout dx, eax\nmov dx, 0xCFC\nin eax, dx\nret`
      try {
        const bin = await compileAssembly(asmCode)
        if (!bin) continue
        const result = await pokeNode(node.endpoint, bin)
        const match = result && result.match(/eax=(\d+)/)
        if (!match) continue
        const val = parseInt(match[1])
        if ((val & 0xFFFF) === 0xFFFF) continue

        const vendor = (val & 0xFFFF).toString(16).toUpperCase().padStart(4, '0')
        const device = ((val >> 16) & 0xFFFF).toString(16).toUpperCase().padStart(4, '0')
        discoveredDevices.push({ slot, vendor, device })
        log.info(`[auto_profile] slot ${slot}: ${vendor}:${device}`)
      } catch (err) {
        continue
      }
    }

    if (discoveredDevices.length === 0) return 'No PCI devices found on this edge.'

    // For each device: read BAR0, probe, generate profile
    const profileDir = path.join(__dirname, '..', 'profiles')
    if (!fs.existsSync(profileDir)) fs.mkdirSync(profileDir, { recursive: true })

    for (const dev of discoveredDevices) {
      const key = `${dev.vendor}:${dev.device}`
      const vendorName = PCI_VENDORS[dev.vendor] || 'Unknown'
      const known = PCI_DEVICES[key]

      // Read BAR0
      const bar0Addr = 0x80000000 | (dev.slot << 11) | 0x10
      const bar0Asm = `BITS 32\nmov eax, ${bar0Addr}\nmov dx, 0xCF8\nout dx, eax\nmov dx, 0xCFC\nin eax, dx\nret`
      let bar0 = null
      try {
        const bar0Bin = await compileAssembly(bar0Asm)
        if (bar0Bin) {
          const bar0Result = await pokeNode(node.endpoint, bar0Bin)
          const bar0Match = bar0Result && bar0Result.match(/eax=(\d+)/)
          if (bar0Match) {
            const bar0Val = parseInt(bar0Match[1]) >>> 0
            bar0 = { raw: bar0Val, isIO: bar0Val & 1, address: bar0Val & 0xFFFFFFF0 }
          }
        }
      } catch (err) {
        log.warn(`[auto_profile] BAR0 read failed for slot ${dev.slot}: ${err.message}`)
      }

      // Ask LLM to generate probe code and profile
      const probeQuestion = `PCI device: vendor=${dev.vendor} (${vendorName}), device=${dev.device}${known ? `, known as ${known.name} (${known.type})` : ', UNKNOWN device'}.
BAR0: ${bar0 ? `0x${bar0.address.toString(16)} (${bar0.isIO ? 'I/O' : 'MMIO'})` : 'not available'}.

Generate a JSON device profile for the POKE system. The profile should include useful operations that read hardware state.

Return ONLY valid JSON (no markdown, no explanation) with this structure:
{
  "vendor_device": "${key}",
  "name": "${known ? known.name : vendorName + ' ' + key}",
  "type": "${known ? known.type : 'unknown'}",
  "arch": "i386",
  "bar0": ${bar0 ? JSON.stringify(bar0) : 'null'},
  "operations": [
    {
      "name": "operation_name",
      "desc": "What this operation does",
      "returns": "What the return value means",
      "asm": "BITS 32\\n...assembly code...\\nret"
    }
  ]
}`

      try {
        const probeMsg = await client.messages.create({
          model: 'claude-haiku-4-5-20251001',
          max_tokens: 1500,
          system: 'You are a hardware expert. Generate device profiles for a bare-metal OS. Output ONLY valid JSON, no markdown fences, no explanation.',
          messages: [{ role: 'user', content: probeQuestion }],
        })

        let profileText = probeMsg.content[0].type === 'text' ? probeMsg.content[0].text.trim() : ''
        profileText = profileText.replace(/^```\w*\n?/, '').replace(/\n?```$/, '').trim()

        const profile = JSON.parse(profileText)

        // Validate and save
        if (profile.vendor_device && profile.name && profile.type) {
          const filename = path.join(profileDir, `${key.replace(':', '_')}.json`)
          fs.writeFileSync(filename, JSON.stringify(profile, null, 2))
          results.push(`Saved: ${key} (${profile.name}) with ${(profile.operations || []).length} operations -> ${filename}`)
          log.info(`[auto_profile] saved profile: ${filename}`)
        } else {
          results.push(`Skipped: ${key} — invalid profile structure`)
        }
      } catch (err) {
        results.push(`Error profiling ${key}: ${err.message}`)
        log.warn(`[auto_profile] error profiling ${key}: ${err.message}`)
      }
    }

    // Reload all profiles so new tools are immediately available
    profiles.clear()
    loadProfiles()

    const summary = `Auto-profile complete for ${target}.\nDiscovered ${discoveredDevices.length} PCI devices.\n${results.join('\n')}\nProfiles reloaded: ${profiles.size} total.`
    log.info(`[auto_profile] done: ${discoveredDevices.length} devices, ${profiles.size} profiles loaded`)
    return summary
  }

  if (toolName === 'reply_text') {
    return toolInput.text
  }

  // ── Factory line shutdown/restart ──
  if (toolName === 'shutdown_line') {
    const target = toolInput.target
    const node = nodes.get(target)
    if (!node) return `Error: edge "${target}" not found`

    try {
      const { shutdownEdge } = require('./transport')
      const result = await shutdownEdge(node.endpoint)
      node.status = 'dead'
      log.info(`[agent] SHUTDOWN ${target}: ${toolInput.reason}`)
      return `Line ${target} SHUTDOWN. Reason: ${toolInput.reason}. QEMU process terminated. Use restart_line to bring it back.`
    } catch (e) {
      return `Shutdown sent to ${target} (${e.message})`
    }
  }

  if (toolName === 'restart_line') {
    const target = toolInput.target
    // Container name: poke-edge-{line-id lowered}-1 → e.g. "line-C" → "poke-edge-line-c-1"
    const containerName = 'poke-edge-' + target.toLowerCase() + '-1'
    try {
      const { execSync } = require('child_process')
      execSync(`docker restart ${containerName}`, { timeout: 30000 })
      log.info(`[agent] RESTART ${target} via docker restart ${containerName}`)
      // Mark alive after boot delay
      setTimeout(() => {
        const node = nodes.get(target)
        if (node) { node.status = 'alive'; node.last_seen = new Date().toISOString() }
      }, 10000)
      return `Line ${target} restarting (container: ${containerName}). Will be online in ~10 seconds.`
    } catch (e) {
      return `Restart failed: ${e.message}`
    }
  }

  // ── Resident binary ──
  if (toolName === 'deploy_resident') {
    const target = toolInput.target || 'x86-qemu'
    const node = nodes.get(target)
    if (!node) return `Error: edge "${target}" not found`
    const bin = await compileAssembly(toolInput.asm_code)
    if (!bin) return 'Error: assembly compilation failed'
    try {
      const { deployResident } = require('./transport')
      const r = await deployResident(node.endpoint, toolInput.slot || 0, toolInput.interval || 1000, bin)
      return `Resident deployed: ${r}`
    } catch (e) { return `Error: ${e.message}` }
  }

  if (toolName === 'stop_resident') {
    const target = toolInput.target || 'x86-qemu'
    const node = nodes.get(target)
    if (!node) return `Error: edge "${target}" not found`
    try {
      const { stopResident } = require('./transport')
      const r = await stopResident(node.endpoint, toolInput.slot || 0)
      return `Slot ${toolInput.slot} stopped: ${r}`
    } catch (e) { return `Error: ${e.message}` }
  }

  // ── Assembly cache tools ──
  if (toolName === 'asm_cache_save') {
    const asmcache = require('./asmcache')
    asmcache.register(toolInput.key, toolInput.template, toolInput.params, toolInput.desc || '', toolInput.tags || [])
    return `Saved template "${toolInput.key}" with params [${toolInput.params.join(',')}]`
  }

  if (toolName === 'asm_cache_run') {
    const asmcache = require('./asmcache')
    const target = toolInput.target || 'x86-qemu'
    const node = nodes.get(target)
    if (!node) return `Error: edge "${target}" not found`
    const bin = await asmcache.resolve(toolInput.key, toolInput.params || {})
    if (!bin) return `Error: template "${toolInput.key}" not found or compile failed`
    try {
      return await pokeNode(node.endpoint, bin)
    } catch (e) { return `Error: ${e.message}` }
  }

  if (toolName === 'asm_cache_list') {
    const asmcache = require('./asmcache')
    return JSON.stringify(asmcache.list(), null, 2)
  }

  // ── Deploy monitor (edge-initiated events) ──
  if (toolName === 'deploy_monitor') {
    const target = toolInput.target || 'x86-qemu'
    const node = nodes.get(target)
    if (!node) return `Error: edge "${target}" not found`
    if (node.arch !== 'i386') return 'Error: deploy_monitor currently supports x86 edges only'

    // Parse condition: "gt:30" → op=0, val=30
    const condMatch = (toolInput.condition || '').match(/^(gt|lt|eq|ne):(\d+)$/)
    if (!condMatch) return 'Error: condition format must be "gt:N", "lt:N", "eq:N", or "ne:N"'
    const condOps = { gt: 0, lt: 1, eq: 2, ne: 3 }
    const condOp = condOps[condMatch[1]]
    const condVal = parseInt(condMatch[2])

    // Compile assembly
    const bin = await compileAssembly(toolInput.asm_code)
    if (!bin) return 'Error: assembly compilation failed'

    // Determine hub IP and port
    const hubPort = parseInt(process.env.PORT || '9000')
    const hubIp = '10.0.2.2'  // QEMU gateway = host

    const intervalMs = toolInput.interval_ms || 1000

    try {
      const { deployMonitor } = require('./transport')
      const result = await deployMonitor(node.endpoint, bin, intervalMs, condOp, condVal, hubIp, hubPort)
      return `Monitor deployed on ${target}: check every ${intervalMs}ms, trigger when ${condMatch[1]}:${condVal}. Edge response: ${result}`
    } catch (err) {
      return `Error deploying monitor: ${err.message}`
    }
  }

  // ── Memory tools ──
  if (toolName === 'memory_save') {
    const entry = memory.addFact(toolInput.fact, 'agent', toolInput.category || 'general')
    return entry ? `Remembered: "${toolInput.fact}"` : 'Already known (duplicate skipped).'
  }

  if (toolName === 'memory_search') {
    const results = memory.searchHistory(toolInput.query)
    if (results.length === 0) return `No memories found for "${toolInput.query}".`
    return results.map(r => `[${r.time}] "${r.command}" → ${r.result || '(no result)'}`).join('\n')
  }

  if (toolName === 'memory_stats') {
    return JSON.stringify(memory.getStats(), null, 2)
  }

  return `Unknown tool: ${toolName}`
}

// ── Result validation: detect anomalous tool results ──
function validateToolResult(toolName, input, resultStr) {
  const warnings = []

  // Raw value interpretation hints (help LLM, don't redirect to firmware)
  if (toolName === 'execute_rv' || toolName === 'execute_x86' || toolName === 'execute_armv6') {
    const asmCode = input?.asm_code || ''
    const a0Match = resultStr.match(/a0=(\d+)/)

    // Temperature register hint
    if (asmCode.match(/tsens|0x60040/i) && a0Match) {
      const raw = parseInt(a0Match[1])
      const approxC = (raw * 0.44 - 28).toFixed(1)
      warnings.push(`Raw TSENS value ${raw}. Approximate conversion: ${approxC}°C (formula: raw*0.44-28, chip-dependent). If value seems wrong, try enabling TSENS first: set bit22 of TSENS_CTRL (0x60040000+88), wait, then read bits[21:14].`)
    }

    // GPIO register hint
    if (asmCode.match(/0x60004/i) && a0Match) {
      const val = parseInt(a0Match[1])
      warnings.push(`GPIO register value 0x${val.toString(16)}. Each bit represents a pin state (bit0=GPIO0, bit1=GPIO1, ...).`)
    }
  }

  return warnings
}

async function agentLoop(command, fromId, targetHint) {
  const Anthropic = require('@anthropic-ai/sdk')
  const client = new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY })

  const edgeList = [...nodes.values()].map(n =>
    `${n.node_id}: arch=${n.arch}, endpoint=${n.endpoint}, caps=${(n.capabilities||[]).join(',')}`
  ).join('\n')

  // ── Build memory context ──
  const memoryContext = memory.buildMemoryContext(command)

  const systemPrompt = `You are JARVIS — the POKE hub agent. You control edge devices by generating and executing machine code.
You have persistent memory. You remember past conversations and user preferences.
When the user tells you something personal or important, save it with memory_save.
When the user references past work ("last time", "before", "remember"), use your memory context below.

Connected edges:
${edgeList}

${targetHint ? `User specified target: ${targetHint}` : ''}

${memoryContext ? `\n--- MEMORY ---\n${memoryContext}\n--- END MEMORY ---\n` : ''}

You have tools to:
- List edges and devices
- Execute raw x86 (NASM), ARM64 (AArch64), ARMv6 (ARM 32-bit), or RISC-V assembly on edges
- Call pre-built device operations directly (no assembly needed!)
- Generate and send images to edges
- Reply with text
${getDeviceToolsSummary()}

Rules:
- For math/computation: use execute_x86, execute_arm, execute_armv6, or execute_rv based on target architecture.
- x86 assembly: MUST start with "BITS 32" on first line. Return result in EAX. End with RET. No sections, no labels. Example: "BITS 32\\nmov eax, 2\\nadd eax, 3\\nret"
- ARM64 assembly: return in X0, end with RET. No .global, .text directives. Just instructions.
- ARMv6 assembly (Pi Zero W): return in R0, end with BX LR. Registers: r0-r12, sp, lr, pc. Use "ldr r0, =0x20200000" for 32-bit constants (GPIO base etc). ARM mode, not Thumb.
- RISC-V assembly: return in a0, end with ret. Registers: a0-a7, s0-s11, t0-t6, sp, ra, zero. For ESP32-C3 edges.
- RISC-V IMPORTANT: ori/andi only support 12-bit signed immediates (-2048 to 2047). For larger values, use li+or/and (R-type).
- If code fails (compile error, wrong result), read the error, fix the code, and retry.
- If a raw sensor value seems off, generate different asm to try another register or approach. You are the intelligence — the edge is just a dumb executor.
- For autonomous monitoring: use deploy_serial_monitor to set up event-driven watches on serial edges.
- For GPIO control (LED, relay, etc.): use set_gpio tool on serial edges.

POKE Philosophy — Edge is dumb, Hub is smart:
- The edge only executes raw machine code and returns register values. No drivers, no OS, no interpretation.
- YOU (the LLM) are the driver. YOU interpret raw values. YOU know the calibration formulas. YOU retry with different asm if needed.
- For sensor readings: generate asm to read the raw register (lw), get the raw value, then YOU convert/interpret it.
- If the raw value seems wrong, try reading a different register, or read the register multiple times for averaging.
- NEVER say "the edge needs a driver" or "this requires firmware support." YOU are the driver.
- Register maps and calibration data come from the device library/profiles — check them before generating asm.
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
- The JS code must export generateFrames(n) returning [{width, height, pixels: Buffer}].

Vision/Multimodal rules:
- Use analyze_image to analyze images (URL or base64) with Claude vision.
- The analysis result is text. You can then decide follow-up actions (draw, execute, reply).

Autonomous monitoring rules:
- Use deploy_autonomous to set up periodic hardware condition checking on an edge.
- Use list_monitors to see all active monitors and their status.
- Use stop_monitor to stop a monitor by ID.

Auto-profiling rules:
- Use auto_profile to scan PCI bus, probe devices, and generate profiles.
- After auto_profile, new device tools become available immediately.`

  const messages = [{ role: 'user', content: command }]
  const steps = []
  let finalResult = null
  const MAX_TURNS = 7

  trace.emit('llm_start', { command: command.slice(0, 200), from: fromId })

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
      trace.emit('llm_text', { turn, text: finalResult.slice(0, 300) })
    }

    if (toolUses.length === 0) {
      log.info(`[agent] done in ${turn + 1} turns`)
      trace.emit('llm_done', { turns: turn + 1, result: finalResult?.slice(0, 200) })
      break
    }

    messages.push({ role: 'assistant', content: response.content })
    const toolResults = []

    for (const tu of toolUses) {
      trace.emit('llm_tool', { turn, tool: tu.name, input: tu.input })

      const result = await executeAgentTool(tu.name, tu.input)
      let resultStr = typeof result === 'string' ? result : JSON.stringify(result)

      // ── Result validation: annotate anomalies so LLM can self-correct ──
      const warnings = validateToolResult(tu.name, tu.input, resultStr)
      if (warnings.length > 0) {
        resultStr += '\n\n⚠️ VALIDATION WARNINGS:\n' + warnings.join('\n')
        log.warn(`[agent] validation: ${warnings.join('; ')}`)
      }

      log.debug(`[agent] ${tu.name} -> ${resultStr.slice(0, 80)}`)

      trace.emit('tool_result', { tool: tu.name, target: tu.input?.target, result: resultStr.slice(0, 200) })

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

  // ── Save to persistent memory + structured context on edges ──
  try {
    const entry = memory.addHistory(command, steps, finalResult)
    memory.syncToEdge(entry).catch(() => {})
    // Structured context: store command on target edge
    const ctx = require('./context')
    const target = targetHint || fromId
    if (target) ctx.storeCommand(target, command, finalResult).catch(() => {})
  } catch (e) {
    log.warn(`[memory] failed to save history: ${e.message}`)
  }

  return { steps, result: finalResult }
}

// ── Task 1 helpers: image fetching and media type detection ──
function detectMediaType(base64Data) {
  // Check magic bytes from base64 prefix
  if (base64Data.startsWith('/9j/')) return 'image/jpeg'
  if (base64Data.startsWith('iVBOR')) return 'image/png'
  if (base64Data.startsWith('R0lGO')) return 'image/gif'
  if (base64Data.startsWith('UklGR')) return 'image/webp'
  return 'image/png' // default
}

function fetchImageAsBase64(url) {
  return new Promise((resolve) => {
    let fetchUrl = url
    if (!fetchUrl.startsWith('http')) fetchUrl = 'http://' + fetchUrl
    const mod = fetchUrl.startsWith('https') ? require('https') : require('http')

    const doFetch = (targetUrl, redirectCount) => {
      if (redirectCount > 5) { resolve(null); return }
      mod.get(targetUrl, { timeout: 15000, headers: { 'User-Agent': 'POKE-Hub/1.0' } }, (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          doFetch(res.headers.location, redirectCount + 1)
          return
        }
        const chunks = []
        let totalBytes = 0
        const MAX_SIZE = 20 * 1024 * 1024 // 20MB max
        res.on('data', (c) => {
          totalBytes += c.length
          if (totalBytes <= MAX_SIZE) chunks.push(c)
        })
        res.on('end', () => {
          if (totalBytes > MAX_SIZE) { resolve(null); return }
          const buf = Buffer.concat(chunks)
          const contentType = res.headers['content-type'] || 'image/png'
          let mediaType = 'image/png'
          if (contentType.includes('jpeg') || contentType.includes('jpg')) mediaType = 'image/jpeg'
          else if (contentType.includes('gif')) mediaType = 'image/gif'
          else if (contentType.includes('webp')) mediaType = 'image/webp'
          else if (contentType.includes('png')) mediaType = 'image/png'
          resolve({ data: buf.toString('base64'), mediaType })
        })
      }).on('error', () => resolve(null))
    }

    doFetch(fetchUrl, 0)
  })
}

module.exports = {
  agentLoop,
  executeAgentTool,
  getAgentTools,
  refreshLibraryTools,
  BASE_TOOLS,
  activeMonitors,
  fetchImageAsBase64,
  detectMediaType,
}
