/**
 * POKE Hub — Real-time Trace System
 *
 * Collects internal events and streams them via SSE to the UI.
 * Shows: health data, monitor triggers, LLM decisions, assembly sent, results.
 */

const { log } = require('./logger')

const MAX_EVENTS = 200
const events = []
const subscribers = []  // SSE response objects
let eventId = 0

/**
 * Add a trace event.
 * @param {string} type - health|trigger|llm_start|llm_tool|llm_result|asm|execute|shutdown|restart|error
 * @param {object} data - event payload
 */
function emit(type, data) {
  const evt = {
    id: ++eventId,
    time: new Date().toISOString(),
    type,
    data,
  }
  events.push(evt)
  if (events.length > MAX_EVENTS) events.shift()

  // Push to all SSE subscribers
  const msg = `id: ${evt.id}\nevent: trace\ndata: ${JSON.stringify(evt)}\n\n`
  for (let i = subscribers.length - 1; i >= 0; i--) {
    try {
      subscribers[i].write(msg)
    } catch (e) {
      subscribers.splice(i, 1)
    }
  }
}

/**
 * Register an SSE subscriber (HTTP response object).
 */
function subscribe(res) {
  subscribers.push(res)
  res.on('close', () => {
    const idx = subscribers.indexOf(res)
    if (idx >= 0) subscribers.splice(idx, 1)
  })
}

/**
 * Get recent events (for initial load).
 */
function recent(count = 50) {
  return events.slice(-count)
}

module.exports = { emit, subscribe, recent }
