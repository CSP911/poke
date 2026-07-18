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

// ── Device profiles (deprecated — use library/ instead) ──
const profiles = new Map()
function loadProfiles() { /* no-op: profiles/ replaced by library/ */ }
function getProfileSummary() { return 'Profiles deprecated. Use library.' }

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
  setMonitorTriggerCallback,
}
