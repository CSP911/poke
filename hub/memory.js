/**
 * POKE Hub — Memory System (JARVIS-style persistent memory)
 *
 * Scalable design:
 * - Monthly file rotation: history-2026-06.json, history-2026-07.json, ...
 * - Hot/warm/cold tiers: recent (full detail) → old (compressed) → archive (summary only)
 * - In-memory index for fast keyword search without full scan
 * - Append-only writes (no full rewrite on every entry)
 */

const fs = require('fs')
const path = require('path')
const { log } = require('./logger')

const MEMORY_DIR = process.env.POKE_MEMORY_DIR || path.join(__dirname, '..', 'data', 'memory')
const FACTS_FILE = path.join(MEMORY_DIR, 'facts.json')
const PATTERNS_FILE = path.join(MEMORY_DIR, 'patterns.json')
const INDEX_FILE = path.join(MEMORY_DIR, 'index.json')

// ── Config ──
const HOT_DAYS = 7          // full detail retention
const WARM_DAYS = 90        // compressed (no steps)
const MAX_PER_MONTH = 2000  // max entries per monthly file
const MAX_FACTS = 200

// ── Ensure data directory exists ──
function ensureDir() {
  if (!fs.existsSync(MEMORY_DIR)) {
    fs.mkdirSync(MEMORY_DIR, { recursive: true })
    log.info(`[memory] created ${MEMORY_DIR}`)
  }
}

// ── Load / Save helpers ──
function loadJSON(filepath, fallback) {
  try {
    if (fs.existsSync(filepath)) {
      return JSON.parse(fs.readFileSync(filepath, 'utf8'))
    }
  } catch (e) {
    log.warn(`[memory] failed to load ${filepath}: ${e.message}`)
  }
  return fallback
}

function saveJSON(filepath, data) {
  ensureDir()
  fs.writeFileSync(filepath, JSON.stringify(data, null, 2))
}

// ── Monthly file management ──
function getMonthKey(date) {
  const d = date instanceof Date ? date : new Date(date)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

function getHistoryFile(monthKey) {
  return path.join(MEMORY_DIR, `history-${monthKey}.json`)
}

function loadMonthHistory(monthKey) {
  return loadJSON(getHistoryFile(monthKey), [])
}

function saveMonthHistory(monthKey, data) {
  saveJSON(getHistoryFile(monthKey), data)
}

// List all available month files, sorted newest first
function listMonthKeys() {
  ensureDir()
  return fs.readdirSync(MEMORY_DIR)
    .filter(f => f.startsWith('history-') && f.endsWith('.json'))
    .map(f => f.replace('history-', '').replace('.json', ''))
    .sort()
    .reverse()
}

// ── In-memory search index ──
// Maps keywords → [{monthKey, id, command}] for fast lookup
let searchIndex = null
let indexDirty = false

function loadIndex() {
  if (searchIndex) return searchIndex
  searchIndex = loadJSON(INDEX_FILE, { entries: {}, lastBuild: null })
  return searchIndex
}

function saveIndex() {
  if (!indexDirty) return
  saveJSON(INDEX_FILE, searchIndex)
  indexDirty = false
}

function indexEntry(entry, monthKey) {
  const idx = loadIndex()
  const words = entry.command.toLowerCase().split(/\s+/).filter(w => w.length > 2)
  // Also index result keywords
  if (entry.result) {
    words.push(...entry.result.toLowerCase().split(/\s+/).filter(w => w.length > 3).slice(0, 10))
  }
  // Index tags
  if (entry.tags) words.push(...entry.tags)

  const ref = { m: monthKey, id: entry.id, cmd: entry.command.slice(0, 80) }

  for (const w of [...new Set(words)]) {
    if (!idx.entries[w]) idx.entries[w] = []
    idx.entries[w].push(ref)
    // Cap per keyword
    if (idx.entries[w].length > 200) {
      idx.entries[w] = idx.entries[w].slice(-200)
    }
  }
  indexDirty = true
}

// ── Compression: reduce old entries ──
function compressEntry(entry) {
  return {
    id: entry.id,
    timestamp: entry.timestamp,
    command: entry.command,
    result: entry.result?.slice(0, 200),
    toolsUsed: entry.toolsUsed,
    edgesUsed: entry.edgesUsed,
    tags: entry.tags,
    // steps removed to save space
  }
}

// Run compression on old monthly files
function compressOldEntries() {
  const now = Date.now()
  const warmCutoff = now - WARM_DAYS * 24 * 60 * 60 * 1000

  for (const mk of listMonthKeys()) {
    const file = getHistoryFile(mk)
    const data = loadJSON(file, [])
    if (data.length === 0) continue

    // Check if oldest entry in this month is old enough
    const oldest = new Date(data[0].timestamp).getTime()
    if (oldest > warmCutoff) continue

    // Check if already compressed (no steps in first entry)
    if (!data[0].steps) continue

    const compressed = data.map(e => {
      const age = now - new Date(e.timestamp).getTime()
      return age > warmCutoff ? compressEntry(e) : e
    })

    const origSize = JSON.stringify(data).length
    const compSize = JSON.stringify(compressed).length
    if (compSize < origSize) {
      saveJSON(file, compressed)
      log.info(`[memory] compressed ${mk}: ${(origSize / 1024).toFixed(1)}KB → ${(compSize / 1024).toFixed(1)}KB`)
    }
  }
}

// ── History: conversation + execution log ──
function addHistory(command, steps, result) {
  const now = new Date()
  const monthKey = getMonthKey(now)
  const history = loadMonthHistory(monthKey)

  const edgesUsed = [...new Set(steps.map(s => s.input?.target).filter(Boolean))]
  const toolsUsed = [...new Set(steps.map(s => s.tool))]

  // Auto-tag
  const tags = []
  if (toolsUsed.some(t => t.includes('execute'))) tags.push('compute')
  if (toolsUsed.some(t => t.includes('draw') || t.includes('stream'))) tags.push('visual')
  if (toolsUsed.some(t => t.includes('device') || t.includes('profile'))) tags.push('hardware')
  if (toolsUsed.includes('fetch_url')) tags.push('external')
  if (toolsUsed.includes('parallel_execute')) tags.push('distributed')
  if (toolsUsed.includes('reply_text') && toolsUsed.length === 1) tags.push('question')
  if (toolsUsed.includes('memory_save') || toolsUsed.includes('memory_search')) tags.push('memory')

  const entry = {
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    timestamp: now.toISOString(),
    command,
    steps: steps.map(s => ({ tool: s.tool, input: s.input, result: s.result?.slice(0, 200) })),
    result: result?.slice(0, 500),
    edgesUsed,
    toolsUsed,
    tags,
  }

  history.push(entry)

  // Cap per month
  if (history.length > MAX_PER_MONTH) {
    history.splice(0, history.length - MAX_PER_MONTH)
  }

  saveMonthHistory(monthKey, history)
  indexEntry(entry, monthKey)
  saveIndex()
  updatePatterns(entry)

  // Periodic compression (every 100 entries)
  const patterns = loadPatterns()
  if (patterns.totalCommands % 100 === 0) {
    try { compressOldEntries() } catch (e) { log.warn(`[memory] compression failed: ${e.message}`) }
  }

  log.info(`[memory] saved: "${command.slice(0, 50)}" → ${monthKey} (${tags.join(',')})`)
  return entry
}

// ── Load history across months ──
// Recent first, with limit
function loadHistory(limit = 100) {
  const months = listMonthKeys()
  const result = []
  for (const mk of months) {
    const data = loadMonthHistory(mk)
    result.unshift(...data)
    if (result.length >= limit) break
  }
  return result.slice(-limit)
}

// Load only current month (fast path)
function loadRecentHistory(count = 20) {
  const mk = getMonthKey(new Date())
  const data = loadMonthHistory(mk)
  return data.slice(-count)
}

// ── Facts ──
function loadFacts() {
  return loadJSON(FACTS_FILE, [])
}

function saveFacts(facts) {
  saveJSON(FACTS_FILE, facts)
}

function addFact(fact, source = 'agent', category = 'general') {
  const facts = loadFacts()

  const isDup = facts.some(f =>
    f.fact.toLowerCase().includes(fact.toLowerCase().slice(0, 30)) ||
    fact.toLowerCase().includes(f.fact.toLowerCase().slice(0, 30))
  )
  if (isDup) {
    log.debug(`[memory] skipping duplicate fact: "${fact.slice(0, 40)}"`)
    return null
  }

  const entry = {
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    timestamp: new Date().toISOString(),
    fact,
    source,
    category,
  }

  facts.push(entry)
  if (facts.length > MAX_FACTS) {
    facts.splice(0, facts.length - MAX_FACTS)
  }

  saveFacts(facts)
  log.info(`[memory] fact saved: "${fact.slice(0, 50)}"`)
  return entry
}

function removeFact(factId) {
  const facts = loadFacts()
  const idx = facts.findIndex(f => f.id === factId)
  if (idx >= 0) {
    facts.splice(idx, 1)
    saveFacts(facts)
    return true
  }
  return false
}

// ── Patterns ──
function loadPatterns() {
  return loadJSON(PATTERNS_FILE, {
    hourCounts: new Array(24).fill(0),
    dayCounts: {},           // "2026-06-01": 5
    commandKeywords: {},
    edgeFreq: {},
    toolFreq: {},
    totalCommands: 0,
  })
}

function savePatterns(patterns) {
  saveJSON(PATTERNS_FILE, patterns)
}

function updatePatterns(entry) {
  const patterns = loadPatterns()
  const date = new Date(entry.timestamp)
  const hour = date.getHours()
  const dayKey = date.toISOString().slice(0, 10)

  patterns.hourCounts[hour] = (patterns.hourCounts[hour] || 0) + 1
  patterns.dayCounts[dayKey] = (patterns.dayCounts[dayKey] || 0) + 1
  patterns.totalCommands = (patterns.totalCommands || 0) + 1

  // Keep only last 90 days of daily counts
  const dayKeys = Object.keys(patterns.dayCounts).sort()
  if (dayKeys.length > 90) {
    for (const k of dayKeys.slice(0, dayKeys.length - 90)) {
      delete patterns.dayCounts[k]
    }
  }

  const words = entry.command.toLowerCase().split(/\s+/).filter(w => w.length > 2)
  for (const w of words) {
    patterns.commandKeywords[w] = (patterns.commandKeywords[w] || 0) + 1
  }

  // Cap keywords at top 500
  const kwEntries = Object.entries(patterns.commandKeywords)
  if (kwEntries.length > 500) {
    kwEntries.sort((a, b) => b[1] - a[1])
    patterns.commandKeywords = Object.fromEntries(kwEntries.slice(0, 500))
  }

  for (const edge of entry.edgesUsed) {
    patterns.edgeFreq[edge] = (patterns.edgeFreq[edge] || 0) + 1
  }

  for (const tool of entry.toolsUsed) {
    patterns.toolFreq[tool] = (patterns.toolFreq[tool] || 0) + 1
  }

  savePatterns(patterns)
}

// ── Recall: find relevant memories ──
function recall(command, limit = 5) {
  const recent = loadRecentHistory(10)
  if (recent.length === 0 && listMonthKeys().length === 0) {
    return { recentHistory: [], relevantHistory: [], facts: [], patterns: null }
  }

  // Fast keyword search using index
  const cmdWords = command.toLowerCase().split(/\s+/).filter(w => w.length > 2)
  let relevantRefs = []

  const idx = loadIndex()
  for (const w of cmdWords) {
    if (idx.entries[w]) {
      relevantRefs.push(...idx.entries[w])
    }
  }

  // Deduplicate and score
  const refMap = new Map()
  for (const ref of relevantRefs) {
    const key = ref.id
    if (refMap.has(key)) {
      refMap.get(key).score++
    } else {
      refMap.set(key, { ...ref, score: 1 })
    }
  }

  // Sort by score, take top N
  const topRefs = [...refMap.values()].sort((a, b) => b.score - a.score).slice(0, limit)

  // Fetch full entries for top refs (grouped by month for efficiency)
  const byMonth = new Map()
  for (const ref of topRefs) {
    if (!byMonth.has(ref.m)) byMonth.set(ref.m, [])
    byMonth.get(ref.m).push(ref.id)
  }

  const relevantEntries = []
  for (const [mk, ids] of byMonth) {
    const data = loadMonthHistory(mk)
    for (const id of ids) {
      const entry = data.find(e => e.id === id)
      if (entry) relevantEntries.push(entry)
    }
  }

  const facts = loadFacts()
  const patterns = loadPatterns()

  return {
    recentHistory: recent.slice(-3).map(formatHistoryEntry),
    relevantHistory: relevantEntries.map(formatHistoryEntry),
    facts: facts.slice(-10),
    patterns: patterns.totalCommands > 0 ? {
      totalCommands: patterns.totalCommands,
      mostActiveHour: patterns.hourCounts.indexOf(Math.max(...patterns.hourCounts)),
      topEdge: Object.entries(patterns.edgeFreq).sort((a, b) => b[1] - a[1])[0]?.[0],
      topKeywords: Object.entries(patterns.commandKeywords).sort((a, b) => b[1] - a[1]).slice(0, 5).map(e => e[0]),
    } : null,
  }
}

function formatHistoryEntry(entry) {
  return {
    time: entry.timestamp,
    command: entry.command,
    result: entry.result?.slice(0, 150),
    tools: entry.toolsUsed,
    edges: entry.edgesUsed,
    tags: entry.tags,
  }
}

// ── Build memory context string for system prompt ──
function buildMemoryContext(command) {
  const { recentHistory, relevantHistory, facts, patterns } = recall(command)
  const parts = []

  if (recentHistory.length > 0) {
    parts.push('=== Recent conversations ===')
    for (const h of recentHistory) {
      parts.push(`[${h.time}] "${h.command}" → ${h.result || '(no text result)'} (tools: ${h.tools?.join(',')})`)
    }
  }

  if (relevantHistory && relevantHistory.length > 0) {
    const unique = relevantHistory.filter(r => !recentHistory.some(h => h.time === r.time))
    if (unique.length > 0) {
      parts.push('\n=== Related past conversations ===')
      for (const h of unique) {
        parts.push(`[${h.time}] "${h.command}" → ${h.result || '(no text result)'}`)
      }
    }
  }

  if (facts.length > 0) {
    parts.push('\n=== Remembered facts ===')
    for (const f of facts) {
      parts.push(`- ${f.fact} (${f.category})`)
    }
  }

  if (patterns) {
    parts.push(`\n=== User patterns ===`)
    parts.push(`Total commands: ${patterns.totalCommands}, Most active hour: ${patterns.mostActiveHour}:00`)
    if (patterns.topEdge) parts.push(`Most used edge: ${patterns.topEdge}`)
    if (patterns.topKeywords?.length) parts.push(`Common topics: ${patterns.topKeywords.join(', ')}`)
  }

  return parts.length > 0 ? parts.join('\n') : ''
}

// ── Search history (across all months) ──
function searchHistory(query, limit = 10) {
  const q = query.toLowerCase()

  // Try index first
  const idx = loadIndex()
  const words = q.split(/\s+/).filter(w => w.length > 2)
  const matchedRefs = new Map()

  for (const w of words) {
    if (idx.entries[w]) {
      for (const ref of idx.entries[w]) {
        if (!matchedRefs.has(ref.id)) {
          matchedRefs.set(ref.id, ref)
        }
      }
    }
  }

  // Fetch from matched months
  if (matchedRefs.size > 0) {
    const byMonth = new Map()
    for (const ref of matchedRefs.values()) {
      if (!byMonth.has(ref.m)) byMonth.set(ref.m, [])
      byMonth.get(ref.m).push(ref.id)
    }

    const results = []
    for (const [mk, ids] of byMonth) {
      const data = loadMonthHistory(mk)
      for (const id of ids) {
        const entry = data.find(e => e.id === id)
        if (entry && (entry.command.toLowerCase().includes(q) || entry.result?.toLowerCase().includes(q))) {
          results.push(entry)
        }
      }
    }
    return results.slice(-limit).map(formatHistoryEntry)
  }

  // Fallback: scan current month
  const recent = loadRecentHistory(100)
  return recent
    .filter(e => e.command.toLowerCase().includes(q) || e.result?.toLowerCase().includes(q))
    .slice(-limit)
    .map(formatHistoryEntry)
}

// ── Stats ──
function getStats() {
  const months = listMonthKeys()
  const facts = loadFacts()
  const patterns = loadPatterns()

  // Count total entries across months
  let totalEntries = 0
  let oldestEntry = null
  let newestEntry = null
  const monthStats = []

  for (const mk of months.slice().reverse()) {
    const data = loadMonthHistory(mk)
    totalEntries += data.length
    if (data.length > 0) {
      if (!oldestEntry) oldestEntry = data[0].timestamp
      newestEntry = data[data.length - 1].timestamp
    }
    const sizeKB = (JSON.stringify(data).length / 1024).toFixed(1)
    monthStats.push({ month: mk, entries: data.length, sizeKB: `${sizeKB}KB` })
  }

  return {
    totalConversations: totalEntries,
    totalFacts: facts.length,
    totalCommands: patterns.totalCommands || 0,
    monthFiles: months.length,
    monthStats,
    oldestEntry,
    newestEntry,
    topEdges: Object.entries(patterns.edgeFreq || {}).sort((a, b) => b[1] - a[1]).slice(0, 5),
    topTools: Object.entries(patterns.toolFreq || {}).sort((a, b) => b[1] - a[1]).slice(0, 5),
    topKeywords: Object.entries(patterns.commandKeywords || {}).sort((a, b) => b[1] - a[1]).slice(0, 10),
    dailyAvg: patterns.totalCommands > 0 && Object.keys(patterns.dayCounts || {}).length > 0
      ? (patterns.totalCommands / Object.keys(patterns.dayCounts).length).toFixed(1)
      : '0',
  }
}

// ── Rebuild index from all history files ──
function rebuildIndex() {
  searchIndex = { entries: {}, lastBuild: new Date().toISOString() }
  indexDirty = true

  for (const mk of listMonthKeys()) {
    const data = loadMonthHistory(mk)
    for (const entry of data) {
      indexEntry(entry, mk)
    }
  }

  saveIndex()
  log.info(`[memory] index rebuilt: ${Object.keys(searchIndex.entries).length} keywords`)
}

// ── Migration: convert old single history.json to monthly format ──
function migrateIfNeeded() {
  const oldFile = path.join(MEMORY_DIR, 'history.json')
  if (!fs.existsSync(oldFile)) return

  const data = loadJSON(oldFile, [])
  if (data.length === 0) {
    fs.unlinkSync(oldFile)
    return
  }

  log.info(`[memory] migrating ${data.length} entries from history.json to monthly files...`)

  // Group by month
  const byMonth = new Map()
  for (const entry of data) {
    const mk = getMonthKey(entry.timestamp || new Date())
    if (!byMonth.has(mk)) byMonth.set(mk, [])
    byMonth.get(mk).push(entry)
  }

  // Save each month
  for (const [mk, entries] of byMonth) {
    const existing = loadMonthHistory(mk)
    existing.push(...entries)
    saveMonthHistory(mk, existing)
  }

  // Rebuild index
  rebuildIndex()

  // Remove old file
  fs.renameSync(oldFile, oldFile + '.migrated')
  log.info(`[memory] migration complete: ${byMonth.size} month files created`)
}

// ── Edge sync: write hub history to edge disk ──
async function syncToEdge(entry) {
  try {
    const { nodes } = require('./nodes')
    const { writeEdgeContext } = require('./transport')

    const payload = `${entry.command.slice(0, 60)} → ${(entry.result || '').slice(0, 50)}`
    const ts = Math.floor(new Date(entry.timestamp).getTime() / 1000)

    for (const [id, node] of nodes) {
      if (node.status !== 'alive') continue
      if (node.endpoint.startsWith('polling:') || node.endpoint.startsWith('tcp:')) continue
      // Check if edge supports context store
      if (!node.health?.ctx && node.health?.ctx !== 0) continue

      try {
        await writeEdgeContext(node.endpoint, 1, ts, payload)
        log.debug(`[memory] synced to edge ${id}`)
      } catch (e) {
        log.debug(`[memory] edge sync failed for ${id}: ${e.message}`)
      }
    }
  } catch (e) {
    // nodes module not loaded yet, skip
  }
}

// ── Recover context from edges (on hub startup) ──
async function recoverFromEdges() {
  try {
    const { nodes } = require('./nodes')
    const { readEdgeContext } = require('./transport')

    for (const [id, node] of nodes) {
      if (node.status !== 'alive' || node.endpoint.startsWith('polling:') || node.endpoint.startsWith('tcp:')) continue
      try {
        const ctx = await readEdgeContext(node.endpoint)
        if (ctx.entries && ctx.entries.length > 0) {
          log.info(`[memory] recovered ${ctx.entries.length} entries from edge ${id}`)
          for (const entry of ctx.entries) {
            addFact(`[edge:${id}] ${entry.data}`, 'edge-recovery', 'context')
          }
        }
      } catch (e) {
        log.debug(`[memory] recovery from ${id} failed: ${e.message}`)
      }
    }
  } catch (e) { /* nodes not loaded */ }
}

// ── Auto-migrate on load ──
try { migrateIfNeeded() } catch (e) { /* ignore */ }

module.exports = {
  addHistory,
  addFact,
  removeFact,
  recall,
  buildMemoryContext,
  searchHistory,
  loadFacts,
  loadHistory,
  loadRecentHistory,
  getStats,
  rebuildIndex,
  compressOldEntries,
  syncToEdge,
  recoverFromEdges,
}
