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

// Known PCI vendors/devices for quick identification
const KNOWN = {
  '8086:1237': { name: 'Intel 440FX Host Bridge', type: 'bridge', skip: true },
  '8086:7000': { name: 'Intel PIIX3 ISA Bridge', type: 'bridge', skip: true },
  '8086:100E': { name: 'Intel 82540EM (e1000)', type: 'network' },
  '1234:1111': { name: 'QEMU Standard VGA', type: 'graphics' },
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

    // Step 5: Try basic probe (read BAR0 first register)
    if (dev.bar0 !== '00000000') {
      const bar0val = parseInt(dev.bar0, 16)
      const isIO = bar0val & 1
      const addr = bar0val & (isIO ? 0xFFFC : 0xFFFFFFF0)

      if (addr > 0) {
        try {
          let probeAsm
          if (isIO) {
            probeAsm = `BITS 32\nmov dx, ${addr}\nin eax, dx\nret`
          } else {
            probeAsm = `BITS 32\nmov ebx, ${addr}\nmov eax, [ebx]\nret`
          }
          const bin = await compileAssembly(probeAsm)
          if (bin) {
            const result = await pokeNode(endpoint, bin)
            const match = result.match(/eax=(\d+)/)
            if (match) {
              profile.probe_result = parseInt(match[1])
              trace.emit('incubate_probe', { edge: nodeId, device: key, result: profile.probe_result })
            }
          }
        } catch (e) {
          log.debug(`[incubate] probe failed for ${key}: ${e.message}`)
        }
      }
    }

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
