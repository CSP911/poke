/**
 * POKE Hub — Device Incubation Engine
 *
 * When an edge comes online, reads its PCI devices and uses LLM to:
 * 1. Identify each device (vendor:device → name, type)
 * 2. Generate probe assembly to test capabilities
 * 3. Execute probes on the edge
 * 4. Build device profiles from results
 *
 * This turns raw hardware into usable AI agent tools automatically.
 */

const { log } = require('./logger')
const { readEdgePCI, pokeNode } = require('./transport')
const { compileAssembly } = require('./compiler')
const trace = require('./trace')
const fs = require('fs')
const path = require('path')

// Load sketch library
const SKETCHES = JSON.parse(fs.readFileSync(path.join(__dirname, 'sketches.json'), 'utf8'))

/**
 * Run a sketch against an edge device.
 * @param {string} endpoint - Edge HTTP endpoint
 * @param {object} sketch - Sketch definition from sketches.json
 * @param {object} params - Parameter values (BAR0, IOBASE, etc.)
 * @returns {object} { value, raw, error }
 */
async function runSketch(endpoint, sketch, params) {
  let asm = sketch.asm
  for (const [k, v] of Object.entries(params)) {
    asm = asm.replace(new RegExp(`\\{\\{${k}\\}\\}`, 'g'), v)
  }
  try {
    const bin = await compileAssembly(asm)
    if (!bin) return { error: 'compile failed' }
    const raw = await pokeNode(endpoint, bin)
    const match = raw.match(/eax=(\d+)/)
    return match ? { value: parseInt(match[1]), raw: raw.trim() } : { raw: raw.trim(), error: 'no eax' }
  } catch (e) {
    return { error: e.message }
  }
}

/**
 * Run all sketches matching a device type.
 * @returns {Array} results with sketch name + value
 */
async function runSketchesForType(endpoint, type, params) {
  const results = []
  const typeSketches = SKETCHES[type] || {}
  const genericSketches = SKETCHES.generic || {}

  for (const [name, sketch] of Object.entries(typeSketches)) {
    // Check if we have required params
    const missing = (sketch.params || []).filter(p => !params[p])
    if (missing.length > 0) continue
    const result = await runSketch(endpoint, sketch, params)
    results.push({ name, desc: sketch.desc, tags: sketch.tag, ...result })
    trace.emit('sketch_run', { sketch: name, type, ...result })
  }

  // Also run generic sketches if params available
  for (const [name, sketch] of Object.entries(genericSketches)) {
    const missing = (sketch.params || []).filter(p => !params[p])
    if (missing.length > 0) continue
    const result = await runSketch(endpoint, sketch, params)
    results.push({ name, desc: sketch.desc, tags: sketch.tag, ...result })
  }

  return results
}

// Known PCI vendors/devices for quick identification
const KNOWN = {
  '8086:1237': { name: 'Intel 440FX Host Bridge', type: 'bridge', skip: true },
  '8086:7000': { name: 'Intel PIIX3 ISA Bridge', type: 'bridge', skip: true },
  '8086:100E': { name: 'Intel 82540EM (e1000)', type: 'network' },
  '1234:1111': { name: 'QEMU Standard VGA', type: 'display' },
  '1AF4:1001': { name: 'VirtIO Block Device', type: 'storage' },
  '1AF4:1005': { name: 'VirtIO RNG', type: 'rng' },
  '1AF4:1002': { name: 'VirtIO Balloon', type: 'memory', skip: true },
}

const PCI_CLASSES = {
  '00': 'unclassified', '01': 'storage', '02': 'network', '03': 'display',
  '04': 'multimedia', '05': 'memory', '06': 'bridge', '07': 'communication',
  '08': 'system', '09': 'input', '0A': 'docking', '0B': 'processor',
  '0C': 'serial', '0D': 'wireless',
}

/**
 * Incubate an edge — scan PCI, identify devices, build profiles.
 * @param {string} nodeId - Edge node ID
 * @param {string} endpoint - Edge HTTP endpoint
 * @returns {object} Incubation report
 */
async function incubate(nodeId, endpoint) {
  log.info(`[incubate] starting for ${nodeId}`)
  trace.emit('incubate_start', { edge: nodeId })

  // Step 1: Read PCI devices
  const pci = await readEdgePCI(endpoint)
  if (!pci.devices || pci.devices.length === 0) {
    log.warn(`[incubate] no PCI devices on ${nodeId}`)
    return { edge: nodeId, status: 'no_devices', devices: [] }
  }

  trace.emit('incubate_pci', { edge: nodeId, count: pci.count, devices: pci.devices })
  log.info(`[incubate] ${pci.count} PCI devices on ${nodeId}`)

  // Step 2: Identify and classify each device
  const report = { edge: nodeId, status: 'complete', devices: [], profiles: [] }

  for (const dev of pci.devices) {
    const key = `${dev.vendor}:${dev.device}`
    const known = KNOWN[key]

    const devInfo = {
      slot: dev.slot,
      id: key,
      class: PCI_CLASSES[dev.class] || 'unknown',
      bar0: dev.bar0,
      name: known?.name || `Unknown ${key}`,
      type: known?.type || PCI_CLASSES[dev.class] || 'unknown',
      skip: known?.skip || false,
    }

    report.devices.push(devInfo)

    if (devInfo.skip) {
      log.debug(`[incubate] skip ${key} (${devInfo.name})`)
      continue
    }

    // Step 3: Check if profile already exists
    const profileDir = path.join(__dirname, '..', 'profiles')
    const profileFile = path.join(profileDir, `${dev.vendor}_${dev.device}.json`)
    if (fs.existsSync(profileFile)) {
      log.debug(`[incubate] profile exists for ${key}`)
      devInfo.profiled = true
      continue
    }

    // Step 4: Create basic profile from PCI info
    const profile = {
      vendor_device: key,
      name: devInfo.name,
      type: devInfo.type,
      arch: 'i386',
      bar0: { address: '0x' + dev.bar0.replace(/^0+/, ''), isIO: (parseInt(dev.bar0, 16) & 1) ? true : false },
      pci_class: `${dev.class}:${dev.subclass}`,
      pci_slot: dev.slot,
      operations: [],
      incubated: new Date().toISOString(),
      edge: nodeId,
    }

    // Step 5: Run sketches for this device type
    const bar0val = parseInt(dev.bar0, 16)
    const isIO = bar0val & 1
    const addr = bar0val & (isIO ? 0xFFFC : 0xFFFFFFF0)
    const sketchParams = {}
    if (addr > 0) {
      if (isIO) sketchParams.IOBASE = '0x' + addr.toString(16)
      else sketchParams.BAR0 = '0x' + addr.toString(16)
    }

    const sketchResults = await runSketchesForType(endpoint, devInfo.type, sketchParams)
    profile.sketch_results = sketchResults

    // Build operations from successful sketches
    for (const sr of sketchResults) {
      if (sr.error) continue
      // Find the original sketch to get the asm template
      const allSketches = { ...SKETCHES[devInfo.type], ...SKETCHES.generic }
      const origSketch = allSketches[sr.name]
      if (!origSketch) continue

      let opAsm = origSketch.asm
      for (const [k, v] of Object.entries(sketchParams)) {
        opAsm = opAsm.replace(new RegExp(`\\{\\{${k}\\}\\}`, 'g'), v)
      }

      profile.operations.push({
        name: sr.name,
        desc: sr.desc,
        returns: origSketch.returns,
        asm: opAsm,
        probe_value: sr.value,
      })
    }

    trace.emit('incubate_sketches', {
      edge: nodeId, device: key,
      ran: sketchResults.length,
      success: sketchResults.filter(r => !r.error).length,
      ops: profile.operations.length,
    })

    // Save profile
    if (!fs.existsSync(profileDir)) fs.mkdirSync(profileDir, { recursive: true })
    fs.writeFileSync(profileFile, JSON.stringify(profile, null, 2))
    report.profiles.push(key)
    devInfo.profiled = true
    log.info(`[incubate] profile created for ${key} (${devInfo.name})`)
    trace.emit('incubate_profile', { edge: nodeId, device: key, name: devInfo.name })
  }

  trace.emit('incubate_done', { edge: nodeId, devices: report.devices.length, profiles: report.profiles.length })
  log.info(`[incubate] done for ${nodeId}: ${report.devices.length} devices, ${report.profiles.length} new profiles`)
  return report
}

module.exports = { incubate }
