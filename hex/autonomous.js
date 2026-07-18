#!/usr/bin/env node
/**
 * HEX Autonomous Federation — self-operating multi-hub system
 *
 * No human input needed. The system:
 * 1. Collects sensor data from all hubs periodically
 * 2. Feeds aggregated cross-site data to LLM
 * 3. LLM decides actions autonomously
 * 4. Actions execute across hubs
 *
 * Usage: node hex/autonomous.js
 */

const { spawn, execSync } = require('child_process')
const fs = require('fs')
const path = require('path')
const net = require('net')
const http = require('http')

const ROOT = path.resolve(__dirname, '..')
const config = JSON.parse(fs.readFileSync(path.join(__dirname, 'federation.json'), 'utf8'))
require('dotenv').config({ path: path.join(ROOT, '.env') })

const HUB_SECRET = process.env.HUB_SECRET || 'poke-secret'
const RV32_BIN = path.join(ROOT, 'kernel', 'rv32', 'poke-rv32.bin')
const processes = []
const hubProcs = []
let cycle = 0

// ── Helpers (same as federation.js) ──

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

// ── Boot infrastructure ──

async function boot() {
  if (!fs.existsSync(RV32_BIN)) execSync(`make -C ${path.join(ROOT, 'kernel', 'rv32')}`, { stdio: 'pipe' })

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

  for (const hub of config.hubs) {
    const p = spawn('node', ['-e', `
      process.env.PORT='${hub.port}'; process.env.HUB_SECRET='${HUB_SECRET}';
      process.env.LOG_LEVEL='error'; process.env.ANTHROPIC_API_KEY='${process.env.ANTHROPIC_API_KEY}';
      require('${path.join(ROOT, 'src', 'hub.js')}');
    `], { stdio: ['pipe', 'pipe', 'pipe'], cwd: ROOT })
    p.stderr.on('data', () => {})
    p.stdout.on('data', () => {})
    hubProcs.push(p)
  }
  for (const hub of config.hubs) await waitForPort(hub.port, 10000)

  let enrolled = 0
  for (const hub of config.hubs) {
    for (const edge of hub.edges) {
      try {
        const r = await httpPost(hub.port, `/enroll?token=${HUB_SECRET}`, {
          node_id: `${hub.id}-${edge.id}`, endpoint: `tcp://127.0.0.1:${edge.port}`,
          arch: 'riscv32', memory_mb: 32, capabilities: ['exec', 'gpio', 'temp'],
          sensors: edge.sensors, description: edge.description,
        })
        if (r.ok) enrolled++
      } catch {}
    }
  }
  await new Promise(r => setTimeout(r, 2000))
  return { alive, enrolled }
}

// ── Collect: direct TCP sensor reads (no LLM, fast) ──

async function collectAll() {
  const serial = require(path.join(ROOT, 'hub', 'serial'))
  const siteData = []

  for (const hub of config.hubs) {
    const site = { hub: hub.id, name: hub.name, port: hub.port, edges: [] }

    for (const edge of hub.edges) {
      const fullId = `${hub.id}-${edge.id}`
      const endpoint = `tcp://127.0.0.1:${edge.port}`
      let temp = null
      try {
        const raw = await serial.serialTemp(endpoint)
        try { temp = JSON.parse(raw)?.celsius } catch {}
      } catch {}

      // Simulate rising temperature for demo
      if (edge.id === 'server-room' && cycle >= 2) temp = 28 + cycle * 1.5
      if (edge.id === 'cold-room' && cycle >= 3) temp = 27 + cycle * 2

      const hasRelay = edge.sensors.some(s => s.includes('relay'))
      site.edges.push({ id: edge.id, fullId, temp, sensors: edge.sensors, hasRelay })
    }
    siteData.push(site)
  }
  return siteData
}

// ── Orchestrator: 1 LLM call → targeted execution ──

async function orchestrate(siteData) {
  // Build situation report
  let report = `[HEX Orchestrator — Cycle ${cycle}]\n\n`
  report += `너는 전체 연합 시스템의 오케스트레이터다. 3개 사이트의 데이터를 보고 판단하라.\n\n`

  for (const site of siteData) {
    report += `Site: ${site.name} (hub: ${site.hub}, port: ${site.port})\n`
    for (const edge of site.edges) {
      const tempStr = edge.temp !== null ? `${edge.temp.toFixed ? edge.temp.toFixed(1) : edge.temp}C` : 'N/A'
      const relay = edge.hasRelay ? 'relay:YES' : 'relay:NO'
      report += `  ${edge.fullId}: ${tempStr} [${edge.sensors.join(', ')}] ${relay}\n`
    }
    report += '\n'
  }

  report += `규칙:
- 28C 이상 = 주의, 30C 이상 = 위험
- 위험한 엣지에 relay가 있으면 → set_gpio target=엣지ID pin=0 value=1 로 팬을 켜라
- relay가 없으면 → "수동 조치 필요" 보고
- 정상이면 → "All clear" 한 줄

중요: 조치가 필요하면 해당 엣지의 set_gpio를 직접 호출하라!
한국어로 간결하게 답변.`

  // Single LLM call via the orchestrator hub (first hub)
  const orchHub = config.hubs[0]
  const from = `${orchHub.id}-${orchHub.edges[0].id}`

  // First, make sure orchestrator hub knows all edge IDs
  // Register remote edges as virtual nodes on orchestrator hub
  for (const hub of config.hubs) {
    if (hub.id === orchHub.id) continue
    for (const edge of hub.edges) {
      try {
        await httpPost(orchHub.port, `/enroll?token=${HUB_SECRET}`, {
          node_id: `${hub.id}-${edge.id}`,
          endpoint: `tcp://127.0.0.1:${edge.port}`,
          arch: 'riscv32', memory_mb: 32,
          capabilities: ['exec', 'gpio', 'temp'],
          sensors: edge.sensors,
        })
      } catch {}
    }
  }

  try {
    const r = await httpPost(orchHub.port, '/relay', { from, command: report })
    return r
  } catch (e) {
    return { error: e.message }
  }
}

// ── Autonomous Loop ──

async function autonomousLoop() {
  cycle++
  const time = new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

  console.log(`\n  ═══ Cycle ${cycle} [${time}] ═══`)

  // 1. Collect
  process.stdout.write('  Collecting...')
  const siteData = await collectAll()
  const readings = siteData.flatMap(s => s.edges.filter(e => e.temp !== null).length)
  console.log(` ${readings.reduce((a,b)=>a+b,0)} readings`)

  // Display
  for (const site of siteData) {
    const temps = site.edges.map(e => e.temp !== null ? `${e.id}:${e.temp}C` : `${e.id}:?`).join('  ')
    console.log(`    ${site.name.padEnd(12)} ${temps}`)
  }

  // 2. Orchestrate: single LLM call → targeted actions
  process.stdout.write('  Orchestrator deciding...')
  const result = await orchestrate(siteData)
  console.log(' done\n')

  if (result.result) {
    result.result.split('\n').forEach(l => console.log(`  HEX: ${l}`))

    // Log actions
    const actions = result.steps?.filter(s => s.tool === 'set_gpio') || []
    if (actions.length > 0) {
      console.log('')
      for (const a of actions) {
        console.log(`  >>> ACTION: ${a.input?.target} GPIO ${a.input?.pin} = ${a.input?.value} — ${a.result}`)
      }
      console.log(`\n  *** ${actions.length} autonomous actions executed ***`)
    }
  } else if (result.error) {
    console.log(`  HEX: Error — ${result.error}`)
  }
}

// ── Main ──

async function main() {
  console.log('')
  console.log('  ╔═══════════════════════════════════════════════╗')
  console.log('  ║   H.E.X. Autonomous Federation               ║')
  console.log('  ║   Self-operating multi-site control           ║')
  console.log('  ║   No human input needed.                      ║')
  console.log('  ╚═══════════════════════════════════════════════╝')

  process.stdout.write('\n  Booting 9 edges + 3 hubs...')
  const { alive, enrolled } = await boot()
  console.log(` ${alive}/9 edges, ${enrolled}/9 enrolled`)

  console.log('\n  Autonomous mode active. Ctrl+C to stop.')
  console.log('  Cycle every 30 seconds: collect → analyze → act\n')

  // First cycle immediately
  await autonomousLoop()

  // Then every 30 seconds
  const interval = setInterval(async () => {
    try { await autonomousLoop() }
    catch (e) { console.log(`  [cycle error: ${e.message}]`) }
  }, 30000)

  // Allow user to type "exit"
  process.stdin.setEncoding('utf8')
  process.stdin.on('data', (data) => {
    if (data.trim() === 'exit') {
      clearInterval(interval)
      console.log('\n  HEX: Autonomous federation shutting down.\n')
      shutdown()
      process.exit(0)
    }
  })
}

function shutdown() {
  for (const p of processes) p.kill('SIGKILL')
  for (const p of hubProcs) p.kill('SIGKILL')
}

process.on('SIGINT', () => { console.log(''); shutdown(); process.exit(0) })
main().catch(e => { console.error('Fatal:', e.message); shutdown(); process.exit(1) })
