#!/usr/bin/env node
/**
 * HEX Federation — Multi-hub interconnected system
 *
 * Launches 3 independent hubs, each with their own QEMU edges.
 * Hubs know about each other and can proxy requests across sites.
 *
 * Architecture:
 *   Hub A (Office :3001)  ←→  Hub B (Factory :3002)  ←→  Hub C (Warehouse :3003)
 *     ↓                         ↓                          ↓
 *   3 edges                   3 edges                    3 edges
 *
 * Usage: node orchestration/federation.js
 */

const { spawn, execSync } = require('child_process')
const fs = require('fs')
const path = require('path')
const net = require('net')
const http = require('http')
const readline = require('readline')

const ROOT = path.resolve(__dirname, '..')
const config = JSON.parse(fs.readFileSync(path.join(__dirname, 'federation.json'), 'utf8'))
require('dotenv').config({ path: path.join(ROOT, '.env') })

const HUB_SECRET = process.env.HUB_SECRET || 'poke-secret'
const RV32_BIN = path.join(ROOT, 'kernel', 'rv32', 'poke-rv32.bin')

const processes = []
const hubProcs = []

// ── Helpers ──

function waitForPort(port, ms) {
  return new Promise((resolve, reject) => {
    const dl = setTimeout(() => reject(new Error('timeout')), ms)
    const tryC = () => {
      const s = new net.Socket()
      s.on('error', () => { s.destroy(); setTimeout(tryC, 200) })
      s.connect(port, '127.0.0.1', () => { clearTimeout(dl); s.destroy(); resolve() })
    }
    tryC()
  })
}

function pokePing(port) {
  return new Promise((resolve, reject) => {
    const s = new net.Socket()
    const t = setTimeout(() => { s.destroy(); reject(new Error('timeout')) }, 5000)
    let buf = Buffer.alloc(0)
    s.connect(port, '127.0.0.1', () => {
      const f = Buffer.alloc(12); f.write('POKE',0); f.writeUInt32LE(4,4); f.write('PING',8); s.write(f)
    })
    s.on('data', d => {
      buf = Buffer.concat([buf, d])
      for (let i = 0; i < buf.length - 8; i++) {
        if (buf[i]===82&&buf[i+1]===69&&buf[i+2]===83&&buf[i+3]===80) {
          const l = buf.readUInt32LE(i+4)
          if (i+8+l<=buf.length) { clearTimeout(t); s.destroy(); resolve(buf.slice(i+8,i+8+l).toString()); return }
        }
      }
    })
    s.on('error', e => { clearTimeout(t); reject(e) })
  })
}

function httpPost(port, urlPath, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body)
    const req = http.request({
      hostname: '127.0.0.1', port, path: urlPath, method: 'POST',
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

function httpGet(port, urlPath) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      hostname: '127.0.0.1', port, path: urlPath, method: 'GET',
      timeout: 10000,
    }, (res) => {
      let d = ''; res.on('data', c => d += c)
      res.on('end', () => { try { resolve(JSON.parse(d)) } catch { resolve({ raw: d }) } })
    })
    req.on('error', reject)
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')) })
    req.end()
  })
}

// ── Boot ──

async function bootEdges() {
  if (!fs.existsSync(RV32_BIN)) {
    execSync(`make -C ${path.join(ROOT, 'kernel', 'rv32')}`, { stdio: 'pipe' })
  }

  for (const hub of config.hubs) {
    for (const edge of hub.edges) {
      const p = spawn('qemu-system-riscv32', [
        '-machine', 'virt', '-cpu', 'rv32', '-m', '32M',
        '-kernel', RV32_BIN, '-nographic', '-bios', 'none',
        '-chardev', `socket,id=u,host=127.0.0.1,port=${edge.port},server=on,wait=off`,
        '-serial', 'chardev:u',
      ], { stdio: ['pipe', 'pipe', 'pipe'] })
      p.stderr.on('data', () => {})
      processes.push(p)
    }
  }

  await new Promise(r => setTimeout(r, 3000))

  let alive = 0
  for (const hub of config.hubs) {
    for (const edge of hub.edges) {
      try { await waitForPort(edge.port, 5000); await pokePing(edge.port); alive++ } catch {}
    }
  }
  return alive
}

async function bootHubs() {
  for (const hub of config.hubs) {
    const env = {
      ...process.env,
      PORT: String(hub.port),
      HUB_SECRET,
      LOG_LEVEL: 'warn',
    }
    const p = spawn('node', ['-e', `
      process.env.PORT = '${hub.port}';
      process.env.HUB_SECRET = '${HUB_SECRET}';
      process.env.LOG_LEVEL = 'warn';
      process.env.ANTHROPIC_API_KEY = '${process.env.ANTHROPIC_API_KEY}';
      require('${path.join(ROOT, 'src', 'hub.js')}');
    `], { stdio: ['pipe', 'pipe', 'pipe'], cwd: ROOT })
    p.stderr.on('data', () => {})
    p.stdout.on('data', () => {})
    hubProcs.push({ hub, proc: p })
  }

  // Wait for all hubs
  for (const hub of config.hubs) {
    await waitForPort(hub.port, 10000)
  }
}

async function enrollEdges() {
  let total = 0
  for (const hub of config.hubs) {
    for (const edge of hub.edges) {
      try {
        const r = await httpPost(hub.port, `/enroll?token=${HUB_SECRET}`, {
          node_id: `${hub.id}-${edge.id}`,
          endpoint: `tcp://127.0.0.1:${edge.port}`,
          arch: 'riscv32', memory_mb: 32,
          capabilities: ['exec', 'gpio', 'temp'],
          sensors: edge.sensors,
          description: edge.description,
        })
        if (r.ok) total++
      } catch {}
    }
  }
  return total
}

// ── Federation: cross-hub proxy ──

async function federatedCommand(command, targetHub) {
  // If targetHub specified, route to that hub
  if (targetHub) {
    const hub = config.hubs.find(h => h.id === targetHub)
    if (!hub) return { error: `Unknown hub: ${targetHub}` }
    const from = `${hub.id}-${hub.edges[0].id}`
    return httpPost(hub.port, '/relay', { from, command })
  }

  // Otherwise, send to all hubs and merge results
  const results = await Promise.all(config.hubs.map(async (hub) => {
    const from = `${hub.id}-${hub.edges[0].id}`
    try {
      const r = await httpPost(hub.port, '/relay', { from, command })
      return { hub: hub.id, name: hub.name, ...r }
    } catch (e) {
      return { hub: hub.id, name: hub.name, error: e.message }
    }
  }))

  return results
}

// ── Display ──

function printFederation() {
  console.log('')
  for (const hub of config.hubs) {
    console.log(`  [${hub.name}] :${hub.port}`)
    for (const edge of hub.edges) {
      console.log(`    └─ ${hub.id}-${edge.id} :${edge.port} [${edge.sensors.join(', ')}]`)
    }
  }
  console.log('')
}

// ── Main ──

async function main() {
  console.log('')
  console.log('  ╔═══════════════════════════════════════════════╗')
  console.log('  ║   H.E.X. Federation                          ║')
  console.log('  ║   3 Hubs × 3 Edges = 9 Nodes                 ║')
  console.log('  ║   Hub-Edge eXecutor — Multi-Site Control      ║')
  console.log('  ╚═══════════════════════════════════════════════╝')

  // Boot
  process.stdout.write('\n  Booting 9 edges...')
  const alive = await bootEdges()
  console.log(` ${alive}/9`)

  process.stdout.write('  Starting 3 hubs...')
  await bootHubs()
  console.log(' ready')

  process.stdout.write('  Enrolling edges...')
  const enrolled = await enrollEdges()
  console.log(` ${enrolled}/9`)
  await new Promise(r => setTimeout(r, 2000))

  printFederation()

  console.log('  Commands:')
  console.log('    "전체 시설 온도"         → 3개 허브 동시 조회')
  console.log('    "@factory 라인1 온도"    → 특정 허브 지정')
  console.log('    "@warehouse 냉장고 팬"   → 특정 허브 제어')
  console.log('    "status"                → 연합 상태 보기')
  console.log('    "exit"                  → 종료')
  console.log('')

  const rl = readline.createInterface({ input: process.stdin, output: process.stdout, prompt: '  You: ' })
  rl.prompt()

  rl.on('line', async (line) => {
    const input = line.trim()
    if (!input) { rl.prompt(); return }
    if (input === 'exit') { console.log('\n  HEX: Federation shutting down.\n'); rl.close(); return }
    if (input === 'status') { printFederation(); rl.prompt(); return }

    // Parse @hub prefix
    let targetHub = null
    let command = input
    const hubMatch = input.match(/^@(\w+)\s+(.+)$/)
    if (hubMatch) {
      targetHub = hubMatch[1]
      command = hubMatch[2]
    }

    process.stdout.write('  HEX: thinking...\r')

    if (targetHub) {
      // Single hub
      const result = await federatedCommand(command, targetHub)
      process.stdout.write('                    \r')
      if (result.result) {
        result.result.split('\n').forEach(l => console.log(`  HEX [${targetHub}]: ${l}`))
        if (result.steps?.length) console.log(`  [${result.steps.length} actions on ${targetHub}]`)
      } else if (result.error) {
        console.log(`  HEX [${targetHub}]: Error — ${result.error}`)
      }
    } else {
      // All hubs (federated)
      const results = await federatedCommand(command)
      process.stdout.write('                    \r')
      for (const r of results) {
        console.log(`  ── ${r.name} (${r.hub}) ──`)
        if (r.result) {
          r.result.split('\n').forEach(l => console.log(`  ${l}`))
          if (r.steps?.length) console.log(`  [${r.steps.length} actions]`)
        } else if (r.error) {
          console.log(`  Error: ${r.error}`)
        }
        console.log('')
      }
    }

    console.log('')
    rl.prompt()
  })

  rl.on('close', () => { shutdown(); process.exit(0) })
}

function shutdown() {
  for (const p of processes) p.kill('SIGKILL')
  for (const { proc } of hubProcs) proc.kill('SIGKILL')
}

process.on('SIGINT', () => { console.log(''); shutdown(); process.exit(0) })
main().catch(e => { console.error('Fatal:', e.message); shutdown(); process.exit(1) })
