/**
 * POKE Hub — Scenario Environment Manager
 *
 * Each scenario defines required edges. Before execution,
 * this module ensures all required QEMU edges are running.
 *
 * Flow: define requirements → check → provision → wait → ready
 */

const { execSync } = require('child_process')
const http = require('http')
const { log } = require('./logger')
const { nodes, enrollNode } = require('./nodes')
const trace = require('./trace')

// ── Scenario Definitions ──
const SCENARIOS = {
  'server-room': {
    name: 'Server Room',
    desc: 'Single edge with temperature monitoring + LLM autonomous control',
    edges: [
      { id: 'x86-qemu', port: 8080, arch: 'i386' },
    ],
  },
  'factory': {
    name: 'Factory Lines',
    desc: '3 production lines — LLM can kill/restart actual QEMU instances',
    edges: [
      { id: 'line-A', port: 8081, arch: 'i386' },
      { id: 'line-B', port: 8082, arch: 'i386' },
      { id: 'line-C', port: 8083, arch: 'i386' },
    ],
  },
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }

function healthCheck(port, timeout = 5000) {
  return new Promise((resolve) => {
    const req = http.get(`http://localhost:${port}/health`, { timeout }, (res) => {
      let d = ''; res.on('data', c => d += c)
      res.on('end', () => { try { resolve(JSON.parse(d)) } catch(e) { resolve(null) } })
    })
    req.on('error', () => resolve(null))
    req.on('timeout', () => { req.destroy(); resolve(null) })
  })
}

/**
 * Ensure all required edges for a scenario are running.
 * @param {string} scenarioId - 'server-room' or 'factory'
 * @returns {object} { ready, edges: [{id, port, status}], provisioned }
 */
async function ensureEnvironment(scenarioId) {
  const scenario = SCENARIOS[scenarioId]
  if (!scenario) return { ready: false, error: `Unknown scenario: ${scenarioId}` }

  log.info(`[env] ensuring environment for "${scenario.name}"`)
  trace.emit('env_start', { scenario: scenarioId, required: scenario.edges.length })

  const result = { scenario: scenarioId, name: scenario.name, edges: [], provisioned: [] }

  for (const edge of scenario.edges) {
    const containerName = `poke-${edge.id}`
    const edgeStatus = { id: edge.id, port: edge.port, status: 'unknown' }

    // Step 1: Check if already alive
    const health = await healthCheck(edge.port)
    if (health && health.status === 'alive') {
      edgeStatus.status = 'alive'
      log.info(`[env] ${edge.id} already alive on :${edge.port}`)
    } else {
      // Step 2: Check if container exists but stopped
      let containerExists = false
      try {
        const state = execSync(`docker inspect -f "{{.State.Status}}" ${containerName} 2>/dev/null`, { timeout: 5000 }).toString().trim()
        containerExists = true
        if (state === 'running') {
          // Running but not responding — give it more time or restart
          log.info(`[env] ${edge.id} container running but not responding, restarting...`)
          execSync(`docker restart ${containerName}`, { timeout: 30000 })
        } else {
          // Stopped — start it
          log.info(`[env] ${edge.id} container stopped, starting...`)
          execSync(`docker start ${containerName}`, { timeout: 10000 })
        }
      } catch (e) {
        containerExists = false
      }

      if (!containerExists) {
        // Step 3: Create new container
        log.info(`[env] ${edge.id} not found, creating on :${edge.port}...`)
        try {
          execSync(`docker rm -f ${containerName} 2>/dev/null`, { timeout: 5000 })
        } catch(e) {}

        try {
          execSync(
            `docker run -d --name ${containerName} -p ${edge.port}:8080 poke-edge`,
            { timeout: 15000 }
          )
          result.provisioned.push(edge.id)
          trace.emit('env_provision', { edge: edge.id, port: edge.port })
        } catch (e) {
          edgeStatus.status = 'failed'
          edgeStatus.error = e.message.slice(0, 100)
          log.warn(`[env] failed to create ${edge.id}: ${e.message.slice(0, 80)}`)
          result.edges.push(edgeStatus)
          continue
        }
      }

      // Step 4: Wait for boot (max 20s)
      edgeStatus.status = 'booting'
      for (let attempt = 0; attempt < 10; attempt++) {
        await sleep(2000)
        const h = await healthCheck(edge.port)
        if (h && h.status === 'alive') {
          edgeStatus.status = 'alive'
          break
        }
      }

      if (edgeStatus.status !== 'alive') {
        edgeStatus.status = 'timeout'
        log.warn(`[env] ${edge.id} failed to boot`)
      }
    }

    // Step 5: Ensure enrolled in hub
    if (edgeStatus.status === 'alive') {
      const endpoint = `http://localhost:${edge.port}`
      if (!nodes.has(edge.id)) {
        enrollNode({ node_id: edge.id, endpoint, arch: edge.arch, memory_mb: 64, capabilities: ['compute'] })
        log.info(`[env] enrolled ${edge.id}`)
      } else {
        // Update endpoint in case port changed
        const existing = nodes.get(edge.id)
        existing.endpoint = endpoint
        existing.status = 'alive'
      }
    }

    result.edges.push(edgeStatus)
  }

  result.ready = result.edges.every(e => e.status === 'alive')
  const status = result.ready ? 'ready' : 'partial'
  log.info(`[env] environment ${status}: ${result.edges.filter(e => e.status === 'alive').length}/${scenario.edges.length} edges`)
  trace.emit('env_done', { scenario: scenarioId, ready: result.ready, provisioned: result.provisioned.length })

  return result
}

/**
 * Tear down scenario environment.
 */
async function teardownEnvironment(scenarioId) {
  const scenario = SCENARIOS[scenarioId]
  if (!scenario) return

  for (const edge of scenario.edges) {
    const containerName = `poke-${edge.id}`
    try {
      execSync(`docker rm -f ${containerName} 2>/dev/null`, { timeout: 10000 })
      log.info(`[env] removed ${containerName}`)
    } catch(e) {}
  }
}

function listScenarios() {
  return Object.entries(SCENARIOS).map(([id, s]) => ({
    id,
    name: s.name,
    desc: s.desc,
    edges: s.edges.length,
  }))
}

module.exports = { ensureEnvironment, teardownEnvironment, listScenarios, SCENARIOS }
