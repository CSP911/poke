/* Hardware checks — `poke check memory`. The edge has no OS, so nearly all
 * RAM is free and MMU is off: a destructive, uncached memory test can run any
 * time. The hub drives it chunk by chunk with a parameter-patched probe from
 * edge/library/<arch>/check-memory so the device stays reachable in between. */
const fs = require('fs')
const path = require('path')
const { execSync } = require('child_process')
const { upload, opClear, opRect, opText } = require('./proto')

const LIB = path.join(__dirname, '..', '..', 'edge', 'library', 'pi4', 'check-memory')
const MB = 1024 * 1024
const CHUNK = 64 * MB
const MAGIC = (n) => (0x7a57a000n + BigInt(n)) * 0x100000001n

function loadProbe() {
  const binPath = path.join(LIB, 'check.bin')
  if (!fs.existsSync(binPath)) execSync('make', { cwd: LIB, stdio: 'ignore' })
  const bin = fs.readFileSync(binPath)
  const off = []
  for (let n = 1; n <= 4; n++) {
    const m = Buffer.alloc(8); m.writeBigUInt64LE(MAGIC(n))
    const i = bin.indexOf(m); if (i < 0) throw new Error(`probe magic ${n} not found`)
    off.push(i)
  }
  return { bin, off }
}

function patched(probe, p) {
  const b = Buffer.from(probe.bin)
  p.forEach((v, i) => b.writeBigUInt64LE(BigInt(v), probe.off[i]))
  return b
}

async function shot(request, probe, p, timeoutMs = 90000) {
  return upload(request, 'EXEC', patched(probe, p), timeoutMs)
}

function ramFromRev(rev) {
  if (!(rev & (1 << 23))) return 0
  return [256, 512, 1024, 2048, 4096, 8192][(rev >> 20) & 7] * MB || 0
}

/* Cortex-A72 cache ECC/parity error registers — the EDAC of this SoC
 * (Pi 4 DRAM has no ECC; the L1/L2 RAMs do). */
function decodeMerrsr(v) {
  const valid = (v >> 31n) & 1n, fatal = (v >> 63n) & 1n
  const repeat = Number((v >> 32n) & 0xffn), other = Number((v >> 40n) & 0xffn)
  const ramid = Number((v >> 24n) & 0x7fn), bank = Number((v >> 18n) & 0xfn), addr = Number(v & 0x3ffffn)
  if (!valid) return 'no errors recorded'
  return `ERRORS: repeat=${repeat} other=${other} fatal=${fatal} ramid=0x${ramid.toString(16)} bank/way=${bank} index=0x${addr.toString(16)}`
}
async function edac(request, opts = {}) {
  const probe = loadProbe()
  const r = await shot(request, probe, [0, 0, opts.clear ? 1 : 0, 3], 15000)
  const c = BigInt((r.match(/cpumerrsr=(0x[0-9a-f]+)/) || [])[1] || '0')
  const l = BigInt((r.match(/l2merrsr=(0x[0-9a-f]+)/) || [])[1] || '0')
  console.log(`CPUMERRSR_EL1 (L1 I/D, TLB RAMs, core 0): 0x${c.toString(16).padStart(16, '0')} — ${decodeMerrsr(c)}`)
  console.log(`L2MERRSR_EL1  (L2 cache RAMs, shared):    0x${l.toString(16).padStart(16, '0')} — ${decodeMerrsr(l)}`)
  if (opts.clear) console.log('(counters cleared)')
  return { cpumerrsr: c, l2merrsr: l }
}

async function memory(request, opts = {}) {
  const probe = loadProbe()
  const cachedFlag = opts.uncached ? 0 : 0x100
  const t0 = Date.now()
  const info = await shot(request, probe, [0, 0, 0, 0], 15000)
  const rev = parseInt((info.match(/rev=(0x[0-9a-f]+)/) || [])[1] || '0', 16)
  const armEnd = parseInt((info.match(/arm_end=(0x[0-9a-f]+)/) || [])[1] || '0', 16)
  const total = ramFromRev(rev)
  if (!armEnd || !total) throw new Error(`could not read memory layout (${info})`)

  /* what we may touch: skip kernel+stack (<16MB), the USB DMA region, GPU/fb, peripherals */
  /* 16-18MB: relocated probe scratch; 0x02000000-0x02400000: GENET packet buffers + xHCI DMA */
  const ranges = [[0x01200000, 0x02000000], [0x02400000, armEnd]]
  if (total > 0x40000000) ranges.push([0x40000000, Math.min(total, 0xFC000000)])
  if (total > 0x100000000) ranges.push([0x100000000, total])
  const spans = ranges.filter(([a, b]) => b > a)
  const testable = spans.reduce((s, [a, b]) => s + (b - a), 0)

  const patterns = opts.full
    ? [['addr', 2, 0n], ['aa55', 1, 0xAAAA5555AAAA5555n], ['00', 1, 0n], ['ff', 1, 0xFFFFFFFFFFFFFFFFn], ['55aa', 1, 0x5555AAAA5555AAAAn]]
    : [['addr', 2, 0n], ['aa55', 1, 0xAAAA5555AAAA5555n]]

  console.log(`RAM ${total / MB} MB (board rev ${rev.toString(16)}), ARM region ends at 0x${armEnd.toString(16)}`)
  console.log(`testing ${(testable / MB).toFixed(0)} MB in ${spans.length} range(s), ${patterns.length} pattern(s)${opts.full ? ' (full)' : ' (quick — add --full)'}, ${opts.uncached ? 'uncached bus cycles' : 'MMU+cache on, chunk flushed to DRAM before verify'}`)
  for (const [a, b] of spans) console.log(`  0x${a.toString(16).padStart(9, '0')} – 0x${b.toString(16).padStart(9, '0')}  ${((b - a) / MB).toFixed(0)} MB`)

  /* on-device progress (DRAW) */
  const W = 1024, BAR_X = 40, BAR_W = W - 80
  const draw = (ops) => request(Buffer.concat([Buffer.from('DRAW'), ...ops]), 3000).catch(() => {})
  await request(Buffer.from('PSTP'), 3000).catch(() => {})
  await draw([opClear(0x00101828), opText(40, 60, 4, 0x0000FF66, 'POKE hardware check: memory'),
              opText(40, 130, 2, 0x00AAAAAA, `${(testable / MB).toFixed(0)} MB testable of ${total / MB} MB`),
              opRect(BAR_X, 300, BAR_W, 30, 0x00303848)])

  const errors = []
  let done = 0, chunks = 0
  const totalWork = testable * patterns.length
  for (const [pname, mode, pat] of patterns) {
    console.log(`\npattern ${pname}:`)
    for (const [a, b] of spans) {
      for (let s = a; s < b; s += CHUNK) {
        const len = Math.min(CHUNK, b - s)
        const r = await shot(request, probe, [s, len, pat, mode | cachedFlag])
        chunks++; done += len
        const pct = Math.floor(done * 100 / totalWork)
        const m = r.match(/ms=(\d+)/); const ms = m ? parseInt(m[1]) : 0
        const rate = ms ? ((len / MB) * 4 / (ms / 1000)).toFixed(0) : '?'
        process.stdout.write(`\r  0x${s.toString(16).padStart(9, '0')} ${(len / MB).toFixed(0).padStart(4)} MB  ${r.startsWith('ok') ? 'ok ' : 'ERR'}  ${ms} ms (${rate} MB/s)   ${pct}%   `)
        if (!r.startsWith('ok')) { errors.push({ pattern: pname, start: s, len, reply: r }); console.log(`\n  !! ${r}`) }
        await draw([opRect(BAR_X, 300, Math.floor(BAR_W * pct / 100), 30, errors.length ? 0x00FF4040 : 0x0000FF66),
                    opRect(40, 360, 900, 40, 0x00101828),
                    opText(40, 360, 2, 0x00AAAAAA, `${pname}  0x${s.toString(16)}  ${pct}%  ${errors.length} error(s)`)])
      }
    }
    console.log('')
  }
  const secs = ((Date.now() - t0) / 1000).toFixed(0)
  const verdict = errors.length ? `FAIL: ${errors.length} bad chunk(s)` : 'PASS: no errors'
  console.log(`\n${verdict} — ${(testable / MB).toFixed(0)} MB × ${patterns.length} patterns, ${chunks} chunks, ${secs}s`)
  for (const e of errors) console.log(`  ${e.pattern} @0x${e.start.toString(16)}: ${e.reply}`)
  await draw([opRect(40, 420, 900, 60, 0x00101828), opText(40, 420, 4, errors.length ? 0x00FF4040 : 0x0000FF66, verdict)])
  return { total, testable, errors, secs }
}

module.exports = { memory, edac }
