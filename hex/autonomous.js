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

// ── Collect: gather sensor data from all hubs ──

async function collectAll() {
  const siteData = []

  for (const hub of config.hubs) {
    const site = { hub: hub.id, name: hub.name, edges: [] }

    for (const edge of hub.edges) {
      const from = `${hub.id}-${edge.id}`
      try {
        const r = await httpPost(hub.port, '/relay', {
          from, command: `read_sensor temp for ${from}`
        })
        // Extract temp from steps
        const tempStep = r.steps?.find(s => s.tool === 'read_sensor')
        let temp = null
        if (tempStep?.result) {
          try { temp = JSON.parse(tempStep.result)?.celsius } catch {}
        }
        // Simulate rising temperature in server-room and cold-room
        if (edge.id === 'server-room' && cycle >= 2) temp = 28 + cycle * 1.5
        if (edge.id === 'cold-room' && cycle >= 3) temp = 27 + cycle * 2
        site.edges.push({ id: edge.id, fullId: from, temp, sensors: edge.sensors })
      } catch {
        site.edges.push({ id: edge.id, fullId: from, temp: null, sensors: edge.sensors })
      }
    }
    siteData.push(site)
  }
  return siteData
}

// ── Analyze: LLM decides what to do ──

async function analyze(siteData) {
  // Build situation report
  let report = `[HEX Federation Status Report — Cycle ${cycle}]\n\n`
  for (const site of siteData) {
    report += `Site: ${site.name} (${site.hub})\n`
    for (const edge of site.edges) {
      const tempStr = edge.temp !== null ? `${edge.temp}C` : 'N/A'
      report += `  ${edge.fullId}: ${tempStr} [${edge.sensors.join(', ')}]\n`
    }
    report += '\n'
  }

  report += `이 데이터를 분석하고 자율적으로 판단하라:

1. 위험한 엣지가 있는가? (28C 이상 = 주의, 30C 이상 = 위험)
2. 조치가 필요하면 즉시 set_gpio로 릴레이(쿨링팬/환기팬)를 직접 켜라.
   - 릴레이가 있는 엣지: relay_2ch 또는 relay_4ch 센서가 있는 엣지
   - 팬 켜기: set_gpio pin=0 value=1 (ON), 끄기: value=0
3. 조치했으면 뭘 했는지 보고하라.
4. 정상이면 "All clear" 한 줄로 끝내라.

중요: 말만 하지 말고, 필요하면 set_gpio 도구를 직접 호출해서 실행하라!
한국어로 답변.`

  // Send to each hub — each hub acts on its own edges
  const results = []
  for (const hub of config.hubs) {
    const from = `${hub.id}-${hub.edges[0].id}`
    try {
      const r = await httpPost(hub.port, '/relay', { from, command: report })
      results.push({ hub: hub.id, name: hub.name, ...r })
    } catch (e) {
      results.push({ hub: hub.id, name: hub.name, error: e.message })
    }
  }
  return results
}

// executeDecision removed — LLM now executes actions directly via set_gpio in analyze()

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

  // 2. Analyze + Act (LLM decides AND executes)
  process.stdout.write('  Analyzing + acting...')
  const results = await analyze(siteData)
  console.log(' done\n')

  let totalActions = 0
  for (const r of results) {
    const actions = r.steps?.filter(s => s.tool === 'set_gpio').length || 0
    totalActions += actions
    const actionMark = actions > 0 ? ` [${actions} actions!]` : ''

    if (r.result) {
      console.log(`  ── ${r.name} ──${actionMark}`)
      r.result.split('\n').forEach(l => console.log(`  ${l}`))

      // Log actual GPIO changes
      if (r.steps) {
        for (const s of r.steps) {
          if (s.tool === 'set_gpio') {
            console.log(`  >>> ACTION: ${s.input?.target} GPIO ${s.input?.pin} = ${s.input?.value} — ${s.result}`)
          }
        }
      }
      console.log('')
    } else if (r.error) {
      console.log(`  ── ${r.name} ── Error: ${r.error}\n`)
    }
  }

  if (totalActions > 0) {
    console.log(`  *** ${totalActions} autonomous actions executed this cycle ***`)
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
