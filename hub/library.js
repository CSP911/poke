/**
 * POKE Hub — Device Library
 *
 * Loads library entries and matches them to edges based on device IDs.
 * When a new edge enrolls, the library automatically finds matching
 * entries and generates agent tools.
 *
 * Matching criteria:
 *   - by_chip:          INFO.chip === entry.match.chip
 *   - by_vendor_device: PCI vendor:device === entry.match.vendor_device
 *   - by_sensor:        INFO.sensors[] includes entry.match.sensor
 */

const fs = require('fs')
const path = require('path')
const { log } = require('./logger')

const LIBRARY_DIR = path.join(__dirname, '..', 'library')

// ── Library state ──
const entries = new Map()   // id → entry JSON
let index = null            // index.json

// ── Load library from disk ──
function loadLibrary() {
  entries.clear()
  const indexPath = path.join(LIBRARY_DIR, 'index.json')
  if (!fs.existsSync(indexPath)) {
    log.warn('[library] index.json not found')
    return
  }

  index = JSON.parse(fs.readFileSync(indexPath, 'utf8'))

  // Load all entry JSON files
  for (const f of fs.readdirSync(LIBRARY_DIR).filter(f => f.endsWith('.json') && f !== 'index.json')) {
    try {
      const entry = JSON.parse(fs.readFileSync(path.join(LIBRARY_DIR, f), 'utf8'))
      if (entry.id) {
        entries.set(entry.id, entry)
      }
    } catch (e) {
      log.warn(`[library] failed to load ${f}: ${e.message}`)
    }
  }

  log.info(`[library] loaded ${entries.size} entries`)
}

// ── Match library entries to an edge based on INFO response ──
function matchEdge(nodeData, info) {
  if (!index) return []

  const matched = new Set()

  // Match by chip
  if (info?.chip && index.by_chip) {
    const chipEntries = index.by_chip[info.chip]
    if (chipEntries) chipEntries.forEach(id => matched.add(id))
  }

  // Match by sensors
  if (info?.sensors && Array.isArray(info.sensors) && index.by_sensor) {
    for (const sensor of info.sensors) {
      const sensorEntries = index.by_sensor[sensor]
      if (sensorEntries) sensorEntries.forEach(id => matched.add(id))
    }
  }

  // Match by vendor:device (for PCI-based edges)
  if (nodeData?.devices && index.by_vendor_device) {
    for (const dev of nodeData.devices) {
      const key = dev.vendor + ':' + dev.device
      const devEntries = index.by_vendor_device[key]
      if (devEntries) devEntries.forEach(id => matched.add(id))
    }
  }

  // Resolve entry IDs to full entries
  const result = []
  for (const id of matched) {
    const entry = entries.get(id)
    if (entry) result.push(entry)
  }

  return result
}

// ── Generate agent tools from matched library entries ──
function generateLibraryTools(nodeId, matchedEntries) {
  const tools = []

  for (const entry of matchedEntries) {
    for (const op of entry.operations || []) {
      const toolName = `${entry.id}__${op.name}`

      // Build tool based on protocol type
      if (op.protocol === 'command') {
        // Firmware-level command (TEMP, GPIO, GPOS, EMON, etc.)
        tools.push({
          name: toolName,
          description: `[${entry.name}] ${op.desc}`,
          input_schema: {
            type: 'object',
            properties: {
              target: { type: 'string', description: 'Edge node ID', default: nodeId },
              ...(op.params || []).reduce((acc, p) => {
                acc[p] = { type: 'number', description: p }
                return acc
              }, {}),
            },
            required: ['target'],
          },
          _library: entry.id,
          _op: op,
          _arch: entry.arch,
          _defaultTarget: nodeId,
        })
      } else if (op.protocol === 'exec' && op.asm) {
        // Assembly-based operation
        tools.push({
          name: toolName,
          description: `[${entry.name}] ${op.desc}`,
          input_schema: {
            type: 'object',
            properties: {
              target: { type: 'string', description: 'Edge node ID', default: nodeId },
              ...(op.params || []).reduce((acc, p) => {
                acc[p] = { type: 'string', description: p }
                return acc
              }, {}),
            },
            required: ['target'],
          },
          _library: entry.id,
          _op: op,
          _asm: op.asm,
          _arch: entry.arch,
          _defaultTarget: nodeId,
        })
      }
    }
  }

  return tools
}

// ── Execute a library tool ──
async function executeLibraryTool(tool, input) {
  const op = tool._op
  const nodeId = input.target || tool._defaultTarget

  // Get node
  const { nodes } = require('./nodes')
  const node = nodes.get(nodeId)
  if (!node) return `Error: edge "${nodeId}" not found`

  if (op.protocol === 'command' && node.endpoint.startsWith('serial://')) {
    const serial = require('./serial')
    const cmd = op.command

    if (cmd === 'TEMP') return await serial.serialTemp(node.endpoint)
    if (cmd === 'GPIO') return await serial.serialGpio(node.endpoint)
    if (cmd === 'GPOS') {
      const pin = input.pin ?? 0
      const val = input.value ?? 0
      return await serial.serialGpioSet(node.endpoint, pin, val)
    }
    if (cmd === 'EMON') {
      const configType = op.config?.type
      if (configType === 1) {
        // GPIO monitor
        const pin = input.pin ?? 9
        const edge = input.edge_trigger ?? 0
        return await serial.serialEventMonitor(node.endpoint, Buffer.from([0x01, pin, edge]))
      }
      if (configType === 2) {
        // Temp monitor
        const opVal = input.op ?? 0
        const threshX10 = Math.round((input.threshold ?? 40) * 10)
        const interval = input.interval_ms ?? 5000
        const buf = Buffer.alloc(6)
        buf[0] = 0x02; buf[1] = opVal
        buf.writeUInt16LE(threshX10, 2)
        buf.writeUInt16LE(interval, 4)
        return await serial.serialEventMonitor(node.endpoint, buf)
      }
    }
    if (cmd === 'ESTOP') return await serial.serialEventStop(node.endpoint)
    if (cmd === 'EXEC') {
      // Requires compiled code — delegate to execute_rv
      return 'Error: use execute_rv for arbitrary code execution'
    }

    return `Error: unknown command "${cmd}"`
  }

  // HTTP-based edges (x86, etc.)
  if (op.protocol === 'exec' && op.asm) {
    // Compile and execute assembly
    const { compileAssembly, compileAssemblyRV } = require('./compiler')
    const { pokeNode } = require('./transport')

    let asm = op.asm
    // Replace params in template
    for (const p of op.params || []) {
      if (input[p] !== undefined) {
        asm = asm.replace(new RegExp(`\\{\\{${p}\\}\\}`, 'g'), input[p])
      }
    }

    const compile = tool._arch === 'riscv32' ? compileAssemblyRV : compileAssembly
    const bin = await compile(asm)
    if (!bin) return 'Error: assembly compilation failed'

    if (node.endpoint.startsWith('serial://')) {
      const { pokeNodeSerial } = require('./serial')
      return await pokeNodeSerial(node.endpoint, bin)
    }
    return await pokeNode(node.endpoint, bin)
  }

  return `Error: unsupported protocol "${op.protocol}" for endpoint "${node.endpoint}"`
}

// ── Probe edge info (works for any connection type) ──
async function probeEdgeInfo(node) {
  if (node.endpoint.startsWith('serial://')) {
    const { serialInfo } = require('./serial')
    return JSON.parse(await serialInfo(node.endpoint))
  }
  if (node.endpoint.startsWith('polling:') || node.endpoint.startsWith('tcp:')) {
    return null  // can't probe
  }
  // HTTP edge
  const http = require('http')
  return new Promise((resolve) => {
    http.get(node.endpoint + '/health', { timeout: 5000 }, (res) => {
      let d = ''; res.on('data', c => d += c)
      res.on('end', () => { try { resolve(JSON.parse(d)) } catch { resolve(null) } })
    }).on('error', () => resolve(null))
  })
}

// ── Incubate: LLM generates library entry for unknown device ──
async function incubateDevice(nodeId, node, info) {
  if (!info) return null

  // Check what's NOT matched
  const matched = matchEdge(node, info)
  const matchedIds = new Set(matched.map(e => e.id))

  // Collect unmatched identifiers
  const unmatched = []
  if (info.chip && (!index?.by_chip?.[info.chip] ||
      !index.by_chip[info.chip].some(id => entries.has(id)))) {
    unmatched.push({ type: 'chip', value: info.chip })
  }
  if (info.sensors) {
    for (const s of info.sensors) {
      if (!index?.by_sensor?.[s] || !index.by_sensor[s].some(id => entries.has(id))) {
        unmatched.push({ type: 'sensor', value: s })
      }
    }
  }

  if (unmatched.length === 0) {
    log.debug(`[incubate] ${nodeId}: all devices already in library`)
    return { status: 'already_matched', matched: matched.length }
  }

  log.info(`[incubate] ${nodeId}: ${unmatched.length} unmatched device(s), asking LLM`)

  // Ask LLM to generate library entry
  const Anthropic = require('@anthropic-ai/sdk')
  const client = new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY })

  const deviceDesc = JSON.stringify(info, null, 2)
  const unmatchedDesc = unmatched.map(u => `${u.type}: ${u.value}`).join(', ')
  const existingOps = matched.flatMap(e => (e.operations || []).map(o => o.name))

  const prompt = `A new edge device connected to the POKE system. Generate a library entry JSON for the unmatched device capabilities.

Edge info:
${deviceDesc}

Unmatched capabilities: ${unmatchedDesc}
Already matched operations: ${existingOps.join(', ') || 'none'}
Connection: ${node.endpoint}
Architecture: ${node.arch || info.arch || 'unknown'}

Generate a library entry JSON with this structure:
{
  "id": "unique_id",
  "name": "Human readable name",
  "chip": "${info.chip || 'null'}",
  "arch": "${info.arch || node.arch || 'unknown'}",
  "match": { "chip": "..." or "sensor": "..." },
  "operations": [
    {
      "name": "operation_name",
      "desc": "What this operation does",
      "type": "sensor|actuator|monitor|compute|control",
      "protocol": "command",
      "command": "FIRMWARE_COMMAND",
      "params": ["optional_param_names"],
      "returns": "What it returns",
      "tags": ["relevant", "tags"]
    }
  ]
}

Rules:
- Use "protocol": "command" for firmware-level commands (listed in info.commands)
- Use "protocol": "exec" with "asm" field for assembly-based operations
- Only create operations for capabilities the device actually has
- For sensors, prefer calibrated firmware commands over raw register access
- Tags should be searchable keywords
- Output ONLY valid JSON, no markdown, no explanation`

  try {
    const response = await client.messages.create({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 1500,
      system: 'You are a hardware expert generating device library entries for the POKE bare-metal system. Output ONLY valid JSON.',
      messages: [{ role: 'user', content: prompt }],
    })

    const text = response.content[0]?.text || ''
    // Extract JSON: find balanced braces
    let jsonStr = null
    const start = text.indexOf('{')
    if (start >= 0) {
      let depth = 0
      for (let i = start; i < text.length; i++) {
        if (text[i] === '{') depth++
        else if (text[i] === '}') { depth--; if (depth === 0) { jsonStr = text.slice(start, i + 1); break } }
      }
    }
    if (!jsonStr) {
      log.warn(`[incubate] LLM returned no JSON for ${nodeId}`)
      return { status: 'llm_failed', error: 'no JSON in response' }
    }

    const entry = JSON.parse(jsonStr)
    if (!entry.id || !entry.operations) {
      log.warn(`[incubate] LLM returned invalid entry for ${nodeId}`)
      return { status: 'llm_failed', error: 'missing id or operations' }
    }

    // Save to library/
    const entryPath = path.join(LIBRARY_DIR, entry.id + '.json')
    fs.writeFileSync(entryPath, JSON.stringify(entry, null, 2))
    entries.set(entry.id, entry)

    // Update index
    if (entry.match?.chip && index) {
      if (!index.by_chip) index.by_chip = {}
      if (!index.by_chip[entry.match.chip]) index.by_chip[entry.match.chip] = []
      if (!index.by_chip[entry.match.chip].includes(entry.id)) {
        index.by_chip[entry.match.chip].push(entry.id)
      }
    }
    if (entry.match?.sensor && index) {
      if (!index.by_sensor) index.by_sensor = {}
      if (!index.by_sensor[entry.match.sensor]) index.by_sensor[entry.match.sensor] = []
      if (!index.by_sensor[entry.match.sensor].includes(entry.id)) {
        index.by_sensor[entry.match.sensor].push(entry.id)
      }
    }
    // Save updated index
    fs.writeFileSync(path.join(LIBRARY_DIR, 'index.json'), JSON.stringify(index, null, 2))

    log.info(`[incubate] created library entry "${entry.id}" with ${entry.operations.length} operations`)
    return { status: 'created', id: entry.id, operations: entry.operations.length }

  } catch (e) {
    log.warn(`[incubate] LLM incubation failed for ${nodeId}: ${e.message}`)
    return { status: 'error', error: e.message }
  }
}

// ── Full incubation pipeline: probe → match → incubate if needed ──
async function incubateEdge(nodeId) {
  const { nodes } = require('./nodes')
  const node = nodes.get(nodeId)
  if (!node) return { status: 'not_found' }

  const info = await probeEdgeInfo(node)
  if (!info) return { status: 'probe_failed' }

  // Store info on node
  node.health = info
  node._chip = info.chip
  node._sensors = info.sensors

  // Try matching first
  const matched = matchEdge(node, info)
  if (matched.length > 0) {
    log.info(`[incubate] ${nodeId}: ${matched.length} entries matched from library`)
  }

  // Incubate unmatched
  const incResult = await incubateDevice(nodeId, node, info)

  // Refresh tools
  const { refreshLibraryTools } = require('./agent')
  const tools = refreshLibraryTools()

  return {
    nodeId,
    info,
    matched: matched.map(e => e.id),
    incubation: incResult,
    totalTools: tools.length,
  }
}

// ── Get summary for system prompt ──
function getLibrarySummary() {
  if (entries.size === 0) return ''
  const lines = ['Available library entries:']
  for (const [id, entry] of entries) {
    const ops = (entry.operations || []).map(o => o.name).join(', ')
    lines.push(`  ${id}: ${entry.name} [${entry.arch}] — ${ops}`)
    // Include register map for LLM to generate asm
    if (entry.registers) {
      for (const [name, reg] of Object.entries(entry.registers)) {
        lines.push(`    REG ${name}: ${reg.addr} — ${reg.desc}`)
      }
    }
    if (entry.calibration) {
      for (const [name, formula] of Object.entries(entry.calibration)) {
        lines.push(`    CAL ${name}: ${formula}`)
      }
    }
  }
  return lines.join('\n')
}

module.exports = {
  loadLibrary,
  matchEdge,
  generateLibraryTools,
  executeLibraryTool,
  getLibrarySummary,
  incubateEdge,
  probeEdgeInfo,
  entries,
}
