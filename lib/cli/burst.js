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
const MAGICS = [0x7a57a001, 0x7a57a002, 0x7a57a003, 0x7a57a004]
const BASE_PORT = 8091

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

{"worker_c": "<C source>",
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
async function burst(task, nEdges = 4, nChunks = 0) {
  nChunks = nChunks || nEdges * 2
  const client = makeClient()
  const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'poke-burst-'))
  const t0 = Date.now()

  console.log(`\n[1/4] HEX planning: ${nChunks} chunks for ${nEdges} edges...`)
  const p = await plan(client, task, nChunks)
  console.log(`  reduce=${p.reduce}, unit=${p.unit || '?'}, params: ${(p.params || []).join(' / ')}`)
  console.log('\n──── generated worker ──────────────────────────')
  console.log(p.worker_c)
  console.log('────────────────────────────────────────────────')

  console.log('\n[2/4] compiling one worker → i386 machine code...')
  const bin = buildWorker(p.worker_c, workDir)
  patchParams(bin, p.chunks[0].map(Number))  /* fail fast if params folded */
  console.log(`  worker: ${bin.length} bytes (× ${p.chunks.length} chunks by patching ${p.chunks[0].length} params)`)

  console.log(`\n[3/4] booting ${nEdges} bare-metal x86 edges (QEMU)...`)
  const edges = await spawnFleet(nEdges, workDir)
  try {
    const tRun = Date.now()
    console.log('\n[4/4] distributing...')
    const results = await runChunks(edges, bin, p.chunks.map((c) => c.map(Number)))
    const wall = Date.now() - tRun

    const failed = results.filter((r) => !r || r.error)
    if (failed.length) throw new Error(`${failed.length} chunks failed`)
    const values = results.map((r) => r.value)
    const answer = (REDUCERS[p.reduce] || REDUCERS.sum)(values)
    const cpuMs = results.reduce((a, r) => a + r.ms, 0)

    console.log('\n══════════════════════════════════════════════')
    console.log(`  answer: ${answer} ${p.unit || ''}`)
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

module.exports = { burst }
