/**
 * POKE Hub — Assembly Cache (reusable parameterized code)
 *
 * Stores assembly templates with keys and parameters.
 * LLM creates code once → cached → reused with different params.
 * Compiled binaries cached too → skip compilation on reuse.
 */

const fs = require('fs')
const path = require('path')
const { log } = require('./logger')
const { compileAssembly } = require('./compiler')

const CACHE_DIR = path.join(__dirname, '..', 'data', 'asmcache')
const CACHE_FILE = path.join(CACHE_DIR, 'registry.json')

// In-memory cache: key → { template, params, desc, tags, compiled: { hash → binary } }
let registry = {}

function ensureDir() {
  if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true })
}

function loadRegistry() {
  try {
    if (fs.existsSync(CACHE_FILE)) {
      registry = JSON.parse(fs.readFileSync(CACHE_FILE, 'utf8'))
      // compiled binaries are not persisted (Buffer doesn't serialize)
      for (const entry of Object.values(registry)) entry.compiled = {}
    }
  } catch (e) { registry = {} }
}

function saveRegistry() {
  ensureDir()
  // Strip compiled binaries before saving (not JSON-serializable)
  const clean = {}
  for (const [k, v] of Object.entries(registry)) {
    clean[k] = { ...v, compiled: undefined }
  }
  fs.writeFileSync(CACHE_FILE, JSON.stringify(clean, null, 2))
}

/**
 * Register an assembly template.
 * @param {string} key - Unique key (e.g. "net_read_mac")
 * @param {string} template - Assembly with {{PARAM}} placeholders
 * @param {string[]} params - Parameter names
 * @param {string} desc - Description
 * @param {string[]} tags - Tags for search
 */
function register(key, template, params, desc, tags) {
  registry[key] = {
    template,
    params: params || [],
    desc: desc || '',
    tags: tags || [],
    created: new Date().toISOString(),
    uses: 0,
    compiled: {},
  }
  saveRegistry()
  log.info(`[asmcache] registered: ${key} (${params.join(',')})`)
  return registry[key]
}

/**
 * Execute a cached template with given parameters.
 * Compiles only if this specific param combination hasn't been seen.
 * @param {string} key - Template key
 * @param {object} paramValues - { BAR0: "0xFEB80000", ... }
 * @returns {Buffer} Compiled binary ready to send to edge
 */
async function resolve(key, paramValues) {
  const entry = registry[key]
  if (!entry) return null

  // Build assembly from template
  let asm = entry.template
  for (const [k, v] of Object.entries(paramValues || {})) {
    asm = asm.replace(new RegExp(`\\{\\{${k}\\}\\}`, 'g'), String(v))
  }

  // Check compiled cache (keyed by final asm string hash)
  const hash = simpleHash(asm)
  if (entry.compiled[hash]) {
    entry.uses++
    log.debug(`[asmcache] cache hit: ${key} (${entry.uses} uses)`)
    return entry.compiled[hash]
  }

  // Compile
  const bin = await compileAssembly(asm)
  if (bin) {
    entry.compiled[hash] = bin
    entry.uses++
    saveRegistry()
    log.debug(`[asmcache] compiled+cached: ${key}`)
  }
  return bin
}

function simpleHash(str) {
  let h = 0
  for (let i = 0; i < str.length; i++) h = ((h << 5) - h + str.charCodeAt(i)) | 0
  return h.toString(36)
}

/**
 * Find templates by tag.
 */
function findByTag(tag) {
  return Object.entries(registry)
    .filter(([_, v]) => v.tags.includes(tag))
    .map(([k, v]) => ({ key: k, ...v, compiled: undefined }))
}

/**
 * List all cached templates.
 */
function list() {
  return Object.entries(registry).map(([k, v]) => ({
    key: k,
    desc: v.desc,
    params: v.params,
    tags: v.tags,
    uses: v.uses,
    created: v.created,
  }))
}

/**
 * Get a specific template.
 */
function get(key) {
  const entry = registry[key]
  if (!entry) return null
  return { key, ...entry, compiled: undefined }
}

/**
 * Auto-register from sketches.json (bootstrap).
 */
function loadSketches() {
  try {
    const sketches = JSON.parse(fs.readFileSync(path.join(__dirname, 'sketches.json'), 'utf8'))
    let count = 0
    for (const [type, ops] of Object.entries(sketches)) {
      for (const [name, sketch] of Object.entries(ops)) {
        const key = `${type}_${name}`
        if (!registry[key]) {
          register(key, sketch.asm, sketch.params || [], sketch.desc, sketch.tag || [type])
          count++
        }
      }
    }
    if (count > 0) log.info(`[asmcache] loaded ${count} sketches into cache`)
  } catch (e) { /* no sketches file */ }
}

// Auto-load on require
loadRegistry()
loadSketches()

module.exports = { register, resolve, findByTag, list, get, loadSketches }
