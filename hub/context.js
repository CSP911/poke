/**
 * POKE Hub — Structured Context System
 *
 * Type-based context storage with indexing.
 * Hub stores structured data on edges, retrieves by type/key.
 *
 * Context Types:
 *   1 = command   — user command + result
 *   2 = event     — monitor trigger, autonomous action
 *   3 = fact      — user preference, device info
 *   4 = metric    — sensor reading, performance data
 *   5 = goal      — goal state, cycle result
 */

const { log } = require('./logger')
const { writeEdgeContext, readEdgeContext } = require('./transport')
const { nodes } = require('./nodes')

const TYPES = {
  COMMAND: 1,
  EVENT: 2,
  FACT: 3,
  METRIC: 4,
  GOAL: 5,
}

const TYPE_NAMES = { 1: 'command', 2: 'event', 3: 'fact', 4: 'metric', 5: 'goal' }

// In-memory index: edgeId → { type → [{seq, timestamp, preview}] }
const index = new Map()

/**
 * Store structured context on an edge.
 */
async function store(edgeId, type, data) {
  const node = nodes.get(edgeId)
  if (!node || node.status !== 'alive') return null
  if (node.endpoint.startsWith('polling:') || node.endpoint.startsWith('tcp:')) return null

  const ts = Math.floor(Date.now() / 1000)
  const payload = typeof data === 'string' ? data : JSON.stringify(data)

  try {
    const result = await writeEdgeContext(node.endpoint, type, ts, payload)

    // Update local index
    if (!index.has(edgeId)) index.set(edgeId, {})
    const edgeIdx = index.get(edgeId)
    if (!edgeIdx[type]) edgeIdx[type] = []
    edgeIdx[type].push({
      timestamp: ts,
      preview: payload.slice(0, 60),
    })

    // Cap index per type
    if (edgeIdx[type].length > 100) edgeIdx[type] = edgeIdx[type].slice(-100)

    log.debug(`[ctx] stored type=${TYPE_NAMES[type] || type} on ${edgeId}`)
    return { ok: true, type, timestamp: ts }
  } catch (e) {
    log.debug(`[ctx] store failed on ${edgeId}: ${e.message}`)
    return null
  }
}

/**
 * Read and filter context from an edge.
 */
async function read(edgeId, options = {}) {
  const node = nodes.get(edgeId)
  if (!node || node.status !== 'alive') return { entries: [] }

  try {
    const raw = await readEdgeContext(node.endpoint)
    let entries = raw.entries || []

    // Filter by type
    if (options.type) {
      entries = entries.filter(e => e.type === options.type)
    }

    // Filter by time range
    if (options.since) {
      entries = entries.filter(e => e.timestamp >= options.since)
    }

    // Search in data
    if (options.search) {
      const q = options.search.toLowerCase()
      entries = entries.filter(e => e.data.toLowerCase().includes(q))
    }

    // Limit
    if (options.limit) {
      entries = entries.slice(0, options.limit)
    }

    return { count: raw.count, entries }
  } catch (e) {
    return { entries: [], error: e.message }
  }
}

/**
 * Get local index for an edge (no network call).
 */
function getIndex(edgeId) {
  const edgeIdx = index.get(edgeId) || {}
  const result = {}
  for (const [type, entries] of Object.entries(edgeIdx)) {
    result[TYPE_NAMES[type] || type] = {
      count: entries.length,
      latest: entries[entries.length - 1],
    }
  }
  return result
}

/**
 * Store a command execution result.
 */
async function storeCommand(edgeId, command, result) {
  return store(edgeId, TYPES.COMMAND, `${command.slice(0, 50)} → ${(result || '').slice(0, 50)}`)
}

/**
 * Store a monitor/autonomous event.
 */
async function storeEvent(edgeId, event) {
  return store(edgeId, TYPES.EVENT, typeof event === 'string' ? event : JSON.stringify(event))
}

/**
 * Store a fact/preference.
 */
async function storeFact(edgeId, fact) {
  return store(edgeId, TYPES.FACT, fact)
}

/**
 * Store a metric reading.
 */
async function storeMetric(edgeId, metric) {
  return store(edgeId, TYPES.METRIC, typeof metric === 'string' ? metric : JSON.stringify(metric))
}

/**
 * Store a goal cycle result.
 */
async function storeGoal(edgeId, goalData) {
  return store(edgeId, TYPES.GOAL, typeof goalData === 'string' ? goalData : JSON.stringify(goalData))
}

module.exports = {
  TYPES,
  TYPE_NAMES,
  store,
  read,
  getIndex,
  storeCommand,
  storeEvent,
  storeFact,
  storeMetric,
  storeGoal,
}
