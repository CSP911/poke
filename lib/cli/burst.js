/* Distributed compute — LLM as compiler AND scheduler (map-reduce).
 *
 *   "count primes below 100M"
 *     → LLM: split plan (chunks) + ONE parameterized C worker
 *     → compiled once to x86 flat binary
 *     → per chunk: patch parameter immediates, dispatch to an edge
 *     → work-queue scheduling over a QEMU fleet (volatile = free retries)
 *     → reduce partial results → answer
 *
 * The worker is volatile machine code generated at request time; edges
 * need no runtime, no job framework — bare metal + 4KB of code. */
const fs = require('fs')
const path = require('path')
const http = require('http')
const os = require('os')
const { spawn, execFileSync } = require('child_process')

const { ROOT, makeClient, textOf } = require('./llm')

const X86_DIR = path.join(ROOT, 'edge/kernel/x86')
const BURST_LD = path.join(__dirname, 'burst.ld')
const CACHE_DIR = path.join(ROOT, 'data/burst-cache')
const MAGICS = [0x7a57a001, 0x7a57a002, 0x7a57a003, 0x7a57a004]
const BASE_PORT = 8091

/* ── worker cache: one verified worker, replayed with new chunk params ──
 * catalog.json entries: { name, description, params, reduce, unit }
 * plus <name>.c (provenance) and <name>.bin (verified binary). The chunk
 * boundaries still come from a cheap per-request planning call, but the
 * worker code — the slow, quality-variable part — is never regenerated. */
function loadCatalog() {
  try { return JSON.parse(fs.readFileSync(path.join(CACHE_DIR, 'catalog.json'), 'utf8')) }
  catch { return [] }
}
function saveCatalog(cat) {
  fs.mkdirSync(CACHE_DIR, { recursive: true })
  fs.writeFileSync(path.join(CACHE_DIR, 'catalog.json'), JSON.stringify(cat, null, 2))
}
function cacheStore(plan, workerC, bin) {
  if (!plan.name) return null
  fs.mkdirSync(CACHE_DIR, { recursive: true })
  const safe = plan.name.toLowerCase().replace(/[^a-z0-9-]/g, '-').slice(0, 40) || 'worker'
  fs.writeFileSync(path.join(CACHE_DIR, `${safe}.c`), workerC)
  fs.writeFileSync(path.join(CACHE_DIR, `${safe}.bin`), bin)
  const cat = loadCatalog().filter((e) => e.name !== safe)
  cat.push({ name: safe, description: plan.description || '', params: plan.params || [],
    reduce: plan.reduce || 'sum', unit: plan.unit || '', created: new Date().toISOString() })
  saveCatalog(cat)
  return safe
}

/* ── 1. plan: task → chunks + one parameterized worker ── */
const PREAMBLE = `/* auto-prepended by poke burst */
static unsigned int __param(unsigned int v) { __asm__ volatile("" : "+r"(v)); return v; }
#define PARAM0 __param(0x7a57a001u)
#define PARAM1 __param(0x7a57a002u)
#define PARAM2 __param(0x7a57a003u)
#define PARAM3 __param(0x7a57a004u)
`

async function plan(client, task, nChunks) {
  const msg = await client.messages.create({
    model: process.env.POKE_MODEL || 'claude-sonnet-5',
    max_tokens: 4000,
    system: `You split a CPU-burst computation into independent chunks for a fleet of
bare-metal x86 (i386) edge devices, and write ONE parameterized C worker that
computes a single chunk. Respond with JSON ONLY:

{"name": "<kebab-case worker family, generic — e.g. count-primes, not primes-below-20m>",
 "description": "<one line: what this worker computes for a chunk, and what PARAM0..n mean>",
 "worker_c": "<C source>",
 "params": ["<meaning of PARAM0>", "<meaning of PARAM1>", ...],
 "chunks": [[p0, p1, ...], ...],
 "reduce": "sum" | "max" | "min",
 "unit": "<what the reduced number counts>"}

Worker HARD CONSTRAINTS (violations crash the device):
- Freestanding C for 32-bit x86: no libc, no #include, no globals, no static
  VARIABLES (static helper functions are fine), no floating point, no 64-bit
  division/modulo (plain 32-bit unsigned arithmetic only; 64-bit add/sub/shift
  in unsigned long long is OK).
- Exactly one entry point, exactly this signature:
    __attribute__((section(".text.main")))
    unsigned int worker(char *out, int *outlen)
  Ignore out/outlen. Return the chunk's partial result (fits in u32).
- Chunk parameters: use PARAM0..PARAM3 (macros provided; each reads a u32
  patched in per chunk). Do NOT define them yourself.
- Keep it small (< 3KB compiled) and loop-based; each chunk should take
  roughly 0.1-5 seconds of CPU.

Chunking rules:
- Exactly ${nChunks} chunks, disjoint, covering the task exactly.
- Each chunk's array lists the values for PARAM0..PARAMn in order.
- Partial results must combine with the declared reduce op into the final
  answer, and each partial must fit in unsigned 32-bit.`,
    messages: [{ role: 'user', content: task }],
  })
  const p = JSON.parse(textOf(msg))
  if (!p.worker_c || !Array.isArray(p.chunks) || !p.chunks.length) throw new Error('bad plan from LLM')
  return p
}

/* ── cache match: reuse a verified worker, plan only the chunk boundaries ── */
async function matchCache(client, task, nChunks, catalog) {
  const listing = catalog.map((e) =>
    `- ${e.name}: ${e.description}\n    params: ${e.params.map((p, i) => `PARAM${i}=${p}`).join(', ')}` +
    `\n    reduce: ${e.reduce}, unit: ${e.unit}`
  ).join('\n')
  const msg = await client.messages.create({
    model: process.env.POKE_MATCH_MODEL || 'claude-sonnet-5',
    max_tokens: 2000,
    system: `You schedule a CPU-burst task onto a fleet using a catalog of cached,
already-verified workers. Do NOT write code. Respond with JSON ONLY.

Catalog:
${listing}

If one cached worker computes exactly what the task asks, split the task into
EXACTLY ${nChunks} disjoint chunks that together cover it, and respond:
{"match":"<name>","chunks":[[p0,p1,...], ...]}
Each chunk array gives the values for PARAM0..PARAMn of that worker, in order.
Each chunk's partial result must fit unsigned 32-bit and combine via the
worker's declared reduce op into the final answer.

If no cached worker fits the task exactly, respond {"match":null}. When unsure,
prefer null — a wrong worker gives a wrong answer.`,
    messages: [{ role: 'user', content: task }],
  })
  try {
    const m = JSON.parse(textOf(msg))
    if (m && m.match && Array.isArray(m.chunks) && m.chunks.length) return m
  } catch {}
  return { match: null }
}

/* ── 2. build: C → flat i386 binary (worker at offset 0) ── */
function buildWorker(source, workDir) {
  fs.mkdirSync(workDir, { recursive: true })
  const src = path.join(workDir, 'worker.c')
  fs.writeFileSync(src, PREAMBLE + source + '\n')
  const obj = path.join(workDir, 'worker.o')
  const elf = path.join(workDir, 'worker.elf')
  const bin = path.join(workDir, 'worker.bin')
  execFileSync('i686-elf-gcc', ['-ffreestanding', '-nostdlib', '-fno-builtin',
    '-fno-stack-protector', '-fno-pic', '-O1', '-Wall', '-ffunction-sections',
    '-c', '-o', obj, src], { stdio: ['ignore', 'pipe', 'pipe'] })
  execFileSync('i686-elf-ld', ['-T', BURST_LD, '-nostdlib', '-o', elf, obj],
    { stdio: ['ignore', 'pipe', 'pipe'] })
  execFileSync('i686-elf-objcopy', ['-O', 'binary', elf, bin], { stdio: 'ignore' })
  return fs.readFileSync(bin)
}

/* ── 3. patch: replace magic immediates with this chunk's values ── */
function patchParams(bin, values) {
  const out = Buffer.from(bin)
  values.forEach((v, i) => {
    const magic = Buffer.alloc(4); magic.writeUInt32LE(MAGICS[i])
    let idx = out.indexOf(magic), n = 0
    while (idx !== -1) { out.writeUInt32LE(v >>> 0, idx); n++; idx = out.indexOf(magic, idx + 4) }
    if (!n) throw new Error(`PARAM${i} not found in binary (compiler folded it?)`)
  })
  return out
}

/* ── 4. fleet: spawn N bare-metal x86 edges under QEMU ── */
function ensureImage() {
  const img = path.join(X86_DIR, 'poke.img')
  if (!fs.existsSync(img)) execFileSync('make', ['-C', X86_DIR], { stdio: 'ignore' })
  return img
}

function httpReq(port, method, p, body, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, method, path: p, timeout: timeoutMs,
      headers: body ? { 'Content-Length': body.length } : {} }, (res) => {
      let d = ''
      res.on('data', (c) => { d += c })
      res.on('end', () => resolve(d))
    })
    req.on('error', reject)
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')) })
    if (body) req.write(body)
    req.end()
  })
}

async function spawnFleet(n, workDir) {
  const img = ensureImage()
  const edges = []
  for (let i = 0; i < n; i++) {
    const port = BASE_PORT + i
    const disk = path.join(workDir, `edge${i}.img`)
    fs.copyFileSync(img, disk)  /* per-edge disk: kernels write VirtIO logs */
    const proc = spawn('qemu-system-i386', [
      '-drive', `format=raw,file=${disk}`, '-m', '64M',
      '-device', 'e1000,netdev=net0',
      '-netdev', `user,id=net0,hostfwd=tcp::${port}-:80`,
      '-display', 'none', '-monitor', 'none', '-serial', 'none',
    ], { stdio: 'ignore' })
    edges.push({ id: i, port, proc, done: 0, ms: 0, failures: 0, dead: false })
  }
  /* wait for all HTTP stacks to come up */
  for (const e of edges) {
    let up = false
    for (let t = 0; t < 40 && !up; t++) {
      try { await httpReq(e.port, 'GET', '/health', null, 1000); up = true }
      catch { await new Promise((r) => setTimeout(r, 500)) }
    }
    if (!up) { e.dead = true; console.log(`  edge${e.id}: failed to boot`) }
  }
  return edges
}

function killFleet(edges) { for (const e of edges) try { e.proc.kill('SIGKILL') } catch {} }

/* ── 5. schedule: work queue over the fleet, retry on failure ── */
async function runChunks(edges, bin, chunks) {
  const queue = chunks.map((values, i) => ({ i, values, tries: 0 }))
  const results = new Array(chunks.length).fill(null)
  const alive = edges.filter((e) => !e.dead)
  if (!alive.length) throw new Error('no edges booted')

  await Promise.all(alive.map(async (edge) => {
    while (true) {
      const job = queue.shift()
      if (!job) return
      const t0 = Date.now()
      try {
        const resp = await httpReq(edge.port, 'POST', '/poke', patchParams(bin, job.values))
        const m = resp.match(/eax=(\d+)/)
        if (!m) throw new Error('no eax in response')
        const ms = Date.now() - t0
        results[job.i] = { value: Number(m[1]), edge: edge.id, ms }
        edge.done++; edge.ms += ms
        console.log(`  chunk ${job.i} [${job.values.join(',')}] → ${m[1]}  (edge${edge.id}, ${ms}ms)`)
      } catch (e) {
        job.tries++
        console.log(`  chunk ${job.i} failed on edge${edge.id} (${e.message}) — requeued`)
        if (job.tries >= 3) { results[job.i] = { error: e.message }; continue }
        queue.push(job)
        if (++edge.failures >= 2) { edge.dead = true; return }  /* volatile: just walk away */
      }
    }
  }))
  return results
}

const REDUCERS = {
  sum: (vs) => vs.reduce((a, b) => a + b, 0),
  max: (vs) => vs.reduce((a, b) => Math.max(a, b)),
  min: (vs) => vs.reduce((a, b) => Math.min(a, b)),
}

/* ── entry ── */
async function burst(task, nEdges = 4, nChunks = 0, opts = {}) {
  nChunks = nChunks || nEdges * 2
  const client = makeClient()
  const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'poke-burst-'))
  const t0 = Date.now()

  let bin, chunks, reduce, unit, fresh = null   /* fresh = plan to cache on success */

  /* ── fast path: a verified cached worker + fresh chunk boundaries ── */
  const catalog = opts.noCache ? [] : loadCatalog()
  if (catalog.length) {
    const m = await matchCache(client, task, nChunks, catalog)
    if (m.match) {
      const entry = catalog.find((e) => e.name === m.match)
      const binPath = path.join(CACHE_DIR, `${m.match}.bin`)
      if (entry && fs.existsSync(binPath)) {
        console.log(`\n⚡ worker cache hit: ${m.match} (${m.chunks.length} chunks)`)
        bin = fs.readFileSync(binPath)
        chunks = m.chunks.map((c) => c.map(Number))
        reduce = entry.reduce; unit = entry.unit
      }
    }
    if (!bin) console.log('(no cached worker fits — generating fresh)')
  }

  /* ── slow path: LLM writes and verifies a new worker ── */
  if (!bin) {
    console.log(`\n[1/4] HEX planning: ${nChunks} chunks for ${nEdges} edges...`)
    const p = await plan(client, task, nChunks)
    console.log(`  worker=${p.name || '?'}, reduce=${p.reduce}, unit=${p.unit || '?'}`)
    console.log('\n──── generated worker ──────────────────────────')
    console.log(p.worker_c)
    console.log('────────────────────────────────────────────────')

    console.log('\n[2/4] compiling one worker → i386 machine code...')
    bin = buildWorker(p.worker_c, workDir)
    patchParams(bin, p.chunks[0].map(Number))  /* fail fast if params folded */
    console.log(`  worker: ${bin.length} bytes (× ${p.chunks.length} chunks by patching ${p.chunks[0].length} params)`)
    chunks = p.chunks.map((c) => c.map(Number))
    reduce = p.reduce; unit = p.unit
    fresh = { plan: p, workerC: p.worker_c }
  }

  console.log(`\n[3/4] booting ${nEdges} bare-metal x86 edges (QEMU)...`)
  const edges = await spawnFleet(nEdges, workDir)
  try {
    const tRun = Date.now()
    console.log('\n[4/4] distributing...')
    const results = await runChunks(edges, bin, chunks)
    const wall = Date.now() - tRun

    const failed = results.filter((r) => !r || r.error)
    if (failed.length) throw new Error(`${failed.length} chunks failed`)
    const values = results.map((r) => r.value)
    const answer = (REDUCERS[reduce] || REDUCERS.sum)(values)
    const cpuMs = results.reduce((a, r) => a + r.ms, 0)

    /* cache the worker only after a fully successful run */
    if (fresh) {
      const saved = cacheStore(fresh.plan, fresh.workerC, bin)
      if (saved) console.log(`\n  cached worker "${saved}" — next similar task skips codegen`)
    }

    console.log('\n══════════════════════════════════════════════')
    console.log(`  answer: ${answer} ${unit || ''}`)
    console.log(`  wall time (distributed): ${(wall / 1000).toFixed(1)}s`)
    console.log(`  cpu time (sum of chunks): ${(cpuMs / 1000).toFixed(1)}s — speedup ×${(cpuMs / wall).toFixed(1)}`)
    for (const e of edges) if (e.done) console.log(`  edge${e.id}: ${e.done} chunks, ${(e.ms / 1000).toFixed(1)}s`)
    console.log(`  total (incl. planning+boot): ${((Date.now() - t0) / 1000).toFixed(1)}s`)
    console.log('══════════════════════════════════════════════')
    return answer
  } finally {
    killFleet(edges)
    fs.rmSync(workDir, { recursive: true, force: true })
  }
}

module.exports = { burst, loadCatalog }
