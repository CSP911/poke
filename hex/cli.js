#!/usr/bin/env node
/**
 * HEX — Interactive Smart Office CLI
 *
 * "The future doesn't need an operating system."
 *
 * Launches 10 QEMU edges, starts the hub, and opens an interactive
 * prompt where you talk to HEX (LLM) in natural language.
 *
 * Usage: node hex/cli.js
 */

const { spawn, execSync } = require('child_process')
const fs = require('fs')
const path = require('path')
const net = require('net')
const http = require('http')
const readline = require('readline')

const ROOT = path.resolve(__dirname, '..')
const config = JSON.parse(fs.readFileSync(path.join(__dirname, 'config.json'), 'utf8'))

// Load env
require('dotenv').config({ path: path.join(ROOT, '.env') })

const HUB_PORT = parseInt(process.env.PORT) || 3333
const HUB_SECRET = process.env.HUB_SECRET || 'poke-secret'
const HUB_URL = `http://localhost:${HUB_PORT}`

const RV32_BIN = path.join(ROOT, 'kernel', 'rv32', 'poke-rv32.bin')
const processes = []
let hubProc = null

// ── Helpers ──

function ensureBinary() {
  if (fs.existsSync(RV32_BIN)) return
  console.log('  Building RV32 kernel...')
  execSync(`make -C ${path.join(ROOT, 'kernel', 'rv32')}`, { stdio: 'pipe' })
}

function waitForPort(port, timeoutMs) {
  return new Promise((resolve, reject) => {
    const deadline = setTimeout(() => reject(new Error('timeout')), timeoutMs)
    const tryConnect = () => {
      const sock = new net.Socket()
      sock.on('error', () => { sock.destroy(); setTimeout(tryConnect, 200) })
      sock.connect(port, '127.0.0.1', () => { clearTimeout(deadline); sock.destroy(); resolve() })
    }
    tryConnect()
  })
}

function httpPost(urlPath, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body)
    const req = http.request({
      hostname: '127.0.0.1', port: HUB_PORT, path: urlPath, method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data), 'Authorization': `Bearer ${HUB_SECRET}` },
      timeout: 120000,
    }, (res) => {
      let d = ''; res.on('data', c => d += c)
      res.on('end', () => { try { resolve(JSON.parse(d)) } catch { resolve({ raw: d }) } })
    })
    req.on('error', reject)
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')) })
    req.write(data); req.end()
  })
}

function pokePing(port) {
  return new Promise((resolve, reject) => {
    const sock = new net.Socket()
    const timeout = setTimeout(() => { sock.destroy(); reject(new Error('timeout')) }, 5000)
    let buf = Buffer.alloc(0)
    sock.connect(port, '127.0.0.1', () => {
      const frame = Buffer.alloc(12)
      frame.write('POKE', 0); frame.writeUInt32LE(4, 4); frame.write('PING', 8)
      sock.write(frame)
    })
    sock.on('data', d => {
      buf = Buffer.concat([buf, d])
      for (let i = 0; i < buf.length - 8; i++) {
        if (buf[i]===0x52 && buf[i+1]===0x45 && buf[i+2]===0x53 && buf[i+3]===0x50) {
          const len = buf.readUInt32LE(i+4)
          if (i+8+len <= buf.length) { clearTimeout(timeout); sock.destroy(); resolve(buf.slice(i+8,i+8+len).toString()); return }
        }
      }
    })
    sock.on('error', e => { clearTimeout(timeout); reject(e) })
  })
}

// ── Launch ──

async function startEdges() {
  ensureBinary()

  for (const edge of config.edges) {
    const proc = spawn('qemu-system-riscv32', [
      '-machine', 'virt', '-cpu', 'rv32', '-m', '32M',
      '-kernel', RV32_BIN, '-nographic', '-bios', 'none',
      '-chardev', `socket,id=uart0,host=127.0.0.1,port=${edge.port},server=on,wait=off`,
      '-serial', 'chardev:uart0',
    ], { stdio: ['pipe', 'pipe', 'pipe'] })
    proc.stderr.on('data', () => {})
    processes.push(proc)
  }

  // Wait for boot
  await new Promise(r => setTimeout(r, 3000))

  let alive = 0
  for (const edge of config.edges) {
    try {
      await waitForPort(edge.port, 5000)
      const resp = await pokePing(edge.port)
      if (resp === 'PONG') alive++
    } catch {}
  }
  return alive
}

async function startHub() {
  hubProc = spawn('node', ['-e', `require('dotenv').config({path:'${path.join(ROOT, '.env')}'}); require('${path.join(ROOT, 'src', 'hub.js')}')`], {
    stdio: ['pipe', 'pipe', 'pipe'],
    cwd: ROOT,
  })
  hubProc.stderr.on('data', () => {})
  hubProc.stdout.on('data', () => {})

  await waitForPort(HUB_PORT, 10000)
}

async function enrollEdges() {
  let ok = 0
  for (const edge of config.edges) {
    try {
      const r = await httpPost(`/enroll?token=${HUB_SECRET}`, {
        node_id: `hex-${edge.id}`,
        endpoint: `tcp://127.0.0.1:${edge.port}`,
        arch: 'riscv32', memory_mb: 32,
        capabilities: ['exec', 'gpio', 'temp'],
        sensors: edge.sensors,
        description: edge.description,
      })
      if (r.ok) ok++
    } catch {}
  }
  return ok
}

// ── Chat ──

async function chat(command) {
  // Use first edge as "from" — the LLM sees all edges
  const from = `hex-${config.edges[0].id}`
  try {
    const result = await httpPost('/relay', { from, command })
    return result
  } catch (e) {
    return { error: e.message }
  }
}

// ── Main ──

async function main() {
  console.log('')
  console.log('  ╔═══════════════════════════════════════════════╗')
  console.log('  ║         H.E.X.                         ║')
  console.log('  ║   Hub-Edge eXecutor       ║')
  console.log('  ║                                               ║')
  console.log('  ║   POKE Smart Office — 10 Edge Devices         ║')
  console.log('  ║   "The future doesn\'t need an operating system." ║')
  console.log('  ╚═══════════════════════════════════════════════╝')
  console.log('')

  // 1. Start edges
  process.stdout.write('  Booting 10 edges...')
  const alive = await startEdges()
  console.log(` ${alive}/10 alive`)

  if (alive === 0) { console.log('  No edges alive. Exiting.'); shutdown(); process.exit(1) }

  // 2. Start hub
  process.stdout.write('  Starting hub...')
  await startHub()
  console.log(' ready')

  // 3. Enroll
  process.stdout.write('  Enrolling edges...')
  const enrolled = await enrollEdges()
  console.log(` ${enrolled}/${config.edges.length}`)

  console.log('')
  console.log('  ─────────────────────────────────────────────')
  console.log('  Rooms: entrance, workspace-1/2, climate,')
  console.log('         server-room, meeting, kitchen,')
  console.log('         parking, rooftop, lobby')
  console.log('  ─────────────────────────────────────────────')
  console.log('')
  console.log('  Talk to HEX. Type "exit" to quit.')
  console.log('')

  // 4. Interactive loop
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: '  You: ',
  })

  rl.prompt()

  rl.on('line', async (line) => {
    const input = line.trim()
    if (!input) { rl.prompt(); return }
    if (input === 'exit' || input === 'quit') { console.log('\n  HEX: Goodbye, sir.\n'); rl.close(); return }

    console.log('')
    process.stdout.write('  HEX: thinking...\r')

    const result = await chat(input)

    // Clear "thinking" line
    process.stdout.write('                        \r')

    if (result.error) {
      console.log(`  HEX: Error — ${result.error}`)
    } else if (result.result) {
      // Format LLM response
      const lines = result.result.split('\n')
      lines.forEach(l => console.log(`  HEX: ${l}`))

      // Show steps summary
      if (result.steps && result.steps.length > 0) {
        console.log('')
        console.log(`  [${result.steps.length} actions taken]`)
      }
    } else {
      console.log('  HEX: (no response)')
    }

    console.log('')
    rl.prompt()
  })

  rl.on('close', () => {
    shutdown()
    process.exit(0)
  })
}

function shutdown() {
  for (const p of processes) p.kill('SIGKILL')
  if (hubProc) hubProc.kill('SIGKILL')
}

process.on('SIGINT', () => { console.log(''); shutdown(); process.exit(0) })
process.on('SIGTERM', () => { shutdown(); process.exit(0) })

main().catch(e => { console.error('Fatal:', e.message); shutdown(); process.exit(1) })
