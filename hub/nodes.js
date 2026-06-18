/**
 * POKE Hub — node registry + health check + device profiles
 */

const http = require('http')
const fs = require('fs')
const path = require('path')
const { log } = require('./logger')
const trace = require('./trace')

// ── Node registry (synchronized writes) ──
const nodes = new Map()
let _registryLock = Promise.resolve()

function enrollNode(nodeData) {
  _registryLock = _registryLock.then(() => {
    nodeData.status = 'alive'
    nodeData.last_seen = new Date().toISOString()
    nodes.set(nodeData.node_id, nodeData)
    log.info(`[enroll] ${nodeData.node_id} @ ${nodeData.endpoint} (${nodeData.arch}, ${nodeData.memory_mb}MB)`)
  })
  return _registryLock
}

// ── Device profile DB ──
const profiles = new Map()

const REQUIRED_PROFILE_FIELDS = ['vendor_device', 'name', 'type']

function validateProfile(p, filename) {
  for (const field of REQUIRED_PROFILE_FIELDS) {
    if (!p[field]) {
      log.warn(`[profiles] ${filename}: missing required field "${field}", skipping`)
      return false
    }
  }
  if (p.operations && !Array.isArray(p.operations)) {
    log.warn(`[profiles] ${filename}: "operations" must be an array, skipping`)
    return false
  }
  if (p.operations) {
    for (let i = 0; i < p.operations.length; i++) {
      const op = p.operations[i]
      if (!op.name || !op.asm) {
        log.warn(`[profiles] ${filename}: operation[${i}] missing "name" or "asm", skipping operation`)
        p.operations.splice(i, 1)
        i--
      }
    }
  }
  return true
}

function loadProfiles() {
  const dir = path.join(__dirname, '..', 'profiles')
  if (!fs.existsSync(dir)) return
  for (const f of fs.readdirSync(dir).filter(f => f.endsWith('.json'))) {
    try {
      const p = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'))
      if (validateProfile(p, f)) {
        profiles.set(p.vendor_device, p)
      }
    } catch (e) {
      log.warn(`[profiles] failed to load ${f}: ${e.message}`)
    }
  }
  log.info(`[profiles] loaded ${profiles.size} device profiles`)
}

function getProfileSummary() {
  if (profiles.size === 0) return 'No device profiles loaded.'
  return [...profiles.values()].map(p =>
    `${p.vendor_device} ${p.name} (${p.type}) BAR0=0x${(p.bar0?.address >>> 0).toString(16)}`
  ).join('\n')
}

// ── Health check + resource monitoring (every 5s) ──
const edgeHistory = new Map()
let healthInterval = null
let onMonitorTrigger = null  // callback: (edgeId, monitorData) => {}

function startHealthCheck() {
  healthInterval = setInterval(() => {
    for (const [id, node] of nodes) {
      if (node.endpoint.startsWith('polling:') || node.endpoint.startsWith('tcp:') || node.endpoint.startsWith('serial://')) {
        // Serial/polling/tcp edges: keep alive, don't probe periodically
        // Serial edges are probed on-demand via /serial/health/:id
        node.status = 'alive'
        node.last_seen = new Date().toISOString()
        continue
      }
      const url = new URL('/health', node.endpoint)
      http.get(url.href, { timeout: 3000 }, (res) => {
        let data = ''
        res.on('data', chunk => data += chunk)
        res.on('end', () => {
          try {
            const health = JSON.parse(data)
            node.status = health.status || 'alive'
            node.last_seen = new Date().toISOString()
            node.health = health

            if (!edgeHistory.has(id)) edgeHistory.set(id, [])
            const history = edgeHistory.get(id)
            history.push({ timestamp: new Date().toISOString(), ...health })
            if (history.length > 60) history.shift()

            // Trace health data
            trace.emit('health', { edge: id, vr: health.vr, monitors: health.monitors, ctx: health.ctx })

            // Auto-detect monitor triggers → fire event
            if (health.monitors && Array.isArray(health.monitors)) {
              for (const mon of health.monitors) {
                if (mon.fired && !node._lastFired?.[mon.id]) {
                  // New trigger detected!
                  log.info(`[health] MONITOR TRIGGERED on ${id}: monitor=${mon.id} val=${mon.val}`)
                  trace.emit('trigger', { edge: id, monitor: mon.id, value: mon.val, triggers: mon.triggers })
                  if (onMonitorTrigger) {
                    onMonitorTrigger(id, mon)
                  }
                }
              }
              // Track fired state to avoid re-firing
              node._lastFired = {}
              for (const mon of health.monitors) {
                node._lastFired[mon.id] = mon.fired
              }
            }
          } catch (e) {
            log.warn(`[health] failed to parse health data for ${id}: ${e.message}`)
          }
        })
      }).on('error', (e) => {
        log.debug(`[health] ${id} unreachable: ${e.message}`)
        node.status = 'dead'
        node.health = null
      }).on('timeout', function() { this.destroy(); node.status = 'dead' })
    }
  }, 5000)
}

function stopHealthCheck() {
  if (healthInterval) {
    clearInterval(healthInterval)
    healthInterval = null
  }
}

function setMonitorTriggerCallback(cb) {
  onMonitorTrigger = cb
}

module.exports = {
  nodes,
  profiles,
  edgeHistory,
  enrollNode,
  loadProfiles,
  getProfileSummary,
  startHealthCheck,
  stopHealthCheck,
  validateProfile,
  setMonitorTriggerCallback,
}
