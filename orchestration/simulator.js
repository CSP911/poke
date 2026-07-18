#!/usr/bin/env node
/**
 * HEX Simulator — Dynamic smart office with events
 *
 * Simulates a living building:
 * - Temperature drifts over time (server room heats up)
 * - Motion sensors fire randomly
 * - Gas sensor spikes in kitchen
 * - HEX (LLM) reacts autonomously
 *
 * Usage: node orchestration/simulator.js
 */

const { spawn, execSync } = require('child_process')
const fs = require('fs')
const path = require('path')
const net = require('net')
const http = require('http')
const readline = require('readline')

const ROOT = path.resolve(__dirname, '..')
const config = JSON.parse(fs.readFileSync(path.join(__dirname, 'config.json'), 'utf8'))
require('dotenv').config({ path: path.join(ROOT, '.env') })

const HUB_PORT = parseInt(process.env.PORT) || 3333
const HUB_SECRET = process.env.HUB_SECRET || 'poke-secret'
const RV32_BIN = path.join(ROOT, 'edge', 'kernel', 'rv32', 'poke-rv32.bin')

const processes = []
let hubProc = null
let simRunning = false

// ══════════════════════════════════════
// ── Building State (the "world")
// ══════════════════════════════════════

const world = {
  time: { hour: 8, minute: 55 },  // start just before work
  rooms: {
    'entrance':     { temp: 22.0, motion: false, door_locked: true },
    'workspace-1':  { temp: 24.0, motion: false, lights: false, fan: false, humidity: 55 },
    'workspace-2':  { temp: 23.5, motion: false, lights: false, air_quality: 400 },
    'climate':      { temp: 24.0, ac_on: false, vent_fan: false },
    'server-room':  { temp: 26.0, motion: false, cooling_fan: false, trend: 0.3 },
    'meeting':      { temp: 23.0, motion: false, lights: false, display: '' },
    'kitchen':      { temp: 23.0, gas_level: 50, exhaust_fan: false },
    'parking':      { temp: 20.0, motion: false, lights: false },
    'rooftop':      { temp: 21.0, humidity: 60, pressure: 1013, wind: 5 },
    'lobby':        { temp: 23.0, motion: false, lights: false, display: '' },
  },
  events: [],       // pending events for HEX
  log: [],          // history
  tick: 0,
}

// ══════════════════════════════════════
// ── Event Scenarios (scripted drama)
// ══════════════════════════════════════

const SCENARIOS = [
  // Tick 2: Someone arrives at work
  { tick: 2, name: '출근 감지', action: () => {
    world.rooms['entrance'].motion = true
    world.rooms['lobby'].motion = true
    return '현관과 로비에서 모션 감지. 아침 9시, 누군가 출근한 것 같습니다.'
  }},

  // Tick 5: Server room heating up
  { tick: 5, name: '서버룸 과열 시작', action: () => {
    world.rooms['server-room'].trend = 0.8  // faster heating
    return `서버룸 온도가 ${world.rooms['server-room'].temp.toFixed(1)}°C로 상승 중입니다. 냉각 확인 필요.`
  }},

  // Tick 8: Meeting starts
  { tick: 8, name: '회의 시작', action: () => {
    world.rooms['meeting'].motion = true
    return '회의실에서 모션 감지. 회의가 시작된 것 같습니다. 조명과 디스플레이를 켜야 할까요?'
  }},

  // Tick 12: Kitchen gas spike!
  { tick: 12, name: '주방 가스 경보!', action: () => {
    world.rooms['kitchen'].gas_level = 800
    world.rooms['kitchen'].temp += 5
    return '⚠️ 주방 가스 센서 급상승! (800ppm) 온도도 상승 중. 즉시 환기 필요!'
  }},

  // Tick 16: Server room critical
  { tick: 16, name: '서버룸 위험', action: () => {
    return `서버룸 온도 ${world.rooms['server-room'].temp.toFixed(1)}°C — 30도 초과 위험! 쿨링 상태를 확인하고 조치해주세요.`
  }},

  // Tick 20: Evening — people leaving
  { tick: 20, name: '퇴근 시간', action: () => {
    world.time.hour = 18
    world.rooms['entrance'].motion = true
    world.rooms['workspace-1'].motion = false
    world.rooms['workspace-2'].motion = false
    world.rooms['meeting'].motion = false
    return '오후 6시, 작업실과 회의실에서 모션이 사라졌습니다. 퇴근 시간인 것 같습니다. 절전 모드로 전환할까요?'
  }},
]

// ══════════════════════════════════════
// ── World Simulation Tick
// ══════════════════════════════════════

function simulateTick() {
  world.tick++

  // Time progression (each tick = 10 minutes)
  world.time.minute += 10
  if (world.time.minute >= 60) { world.time.minute = 0; world.time.hour++ }
  if (world.time.hour >= 24) world.time.hour = 0

  // Server room: temp drifts based on trend (cooling fan reduces it)
  const sr = world.rooms['server-room']
  if (sr.cooling_fan) sr.trend = -0.5
  sr.temp += sr.trend * (0.5 + Math.random() * 0.5)
  sr.temp = Math.max(20, Math.min(45, sr.temp))

  // Kitchen: gas level decays if exhaust is on
  const kt = world.rooms['kitchen']
  if (kt.exhaust_fan) {
    kt.gas_level = Math.max(30, kt.gas_level - 100)
    kt.temp = Math.max(22, kt.temp - 0.5)
  }

  // Outdoor temp follows time of day
  const rt = world.rooms['rooftop']
  rt.temp = 18 + 8 * Math.sin((world.time.hour - 6) * Math.PI / 12)
  rt.humidity = 50 + 20 * Math.cos((world.time.hour - 14) * Math.PI / 12)

  // Random temp drift in all rooms
  for (const [name, room] of Object.entries(world.rooms)) {
    if (name === 'server-room' || name === 'rooftop') continue
    if (room.temp !== undefined) {
      room.temp += (Math.random() - 0.5) * 0.3
      // AC effect
      if (world.rooms['climate'].ac_on && room.temp > 24) room.temp -= 0.3
    }
  }

  // Check for scripted events
  const event = SCENARIOS.find(s => s.tick === world.tick)
  if (event) {
    const msg = event.action()
    world.events.push({ tick: world.tick, name: event.name, message: msg })
    return { event: event.name, message: msg }
  }

  return null
}

// ══════════════════════════════════════
// ── Infrastructure (same as cli.js)
// ══════════════════════════════════════

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

async function chat(command) {
  const from = `hex-${config.edges[0].id}`
  try { return await httpPost('/relay', { from, command }) }
  catch (e) { return { error: e.message } }
}

async function setup() {
  // Build
  if (!fs.existsSync(RV32_BIN)) execSync(`make -C ${path.join(ROOT, 'edge', 'kernel', 'rv32')}`, { stdio: 'pipe' })

  // Edges
  for (const edge of config.edges) {
    const p = spawn('qemu-system-riscv32', [
      '-machine', 'virt', '-cpu', 'rv32', '-m', '32M',
      '-kernel', RV32_BIN, '-nographic', '-bios', 'none',
      '-chardev', `socket,id=u,host=127.0.0.1,port=${edge.port},server=on,wait=off`,
      '-serial', 'chardev:u',
    ], { stdio: ['pipe', 'pipe', 'pipe'] })
    p.stderr.on('data', () => {})
    processes.push(p)
  }
  await new Promise(r => setTimeout(r, 3000))

  let alive = 0
  for (const e of config.edges) {
    try { await waitForPort(e.port, 5000); await pokePing(e.port); alive++ } catch {}
  }

  // Hub
  hubProc = spawn('node', ['-e', `require('dotenv').config({path:'${path.join(ROOT,'.env')}'}); require('${path.join(ROOT,'src','hub.js')}')`], {
    stdio: ['pipe', 'pipe', 'pipe'], cwd: ROOT,
  })
  hubProc.stderr.on('data', () => {})
  hubProc.stdout.on('data', () => {})
  await waitForPort(HUB_PORT, 10000)

  // Enroll
  let ok = 0
  for (const e of config.edges) {
    try {
      const r = await httpPost(`/enroll?token=${HUB_SECRET}`, {
        node_id: `hex-${e.id}`, endpoint: `tcp://127.0.0.1:${e.port}`,
        arch: 'riscv32', memory_mb: 32, capabilities: ['exec','gpio','temp'],
        sensors: e.sensors, description: e.description,
      })
      if (r.ok) ok++
    } catch {}
  }

  return { alive, enrolled: ok }
}

function shutdown() {
  simRunning = false
  for (const p of processes) p.kill('SIGKILL')
  if (hubProc) hubProc.kill('SIGKILL')
}

// ══════════════════════════════════════
// ── Display
// ══════════════════════════════════════

function renderStatus() {
  const t = world.time
  const timeStr = `${String(t.hour).padStart(2,'0')}:${String(t.minute).padStart(2,'0')}`

  console.log(`\n  --- ${timeStr} (tick ${world.tick}) ---`)
  console.log(`  server-room: ${world.rooms['server-room'].temp.toFixed(1)} C  fan:${world.rooms['server-room'].cooling_fan ? 'ON' : 'OFF'}`)
  console.log(`  kitchen:     gas=${world.rooms['kitchen'].gas_level}ppm  exhaust:${world.rooms['kitchen'].exhaust_fan ? 'ON' : 'OFF'}`)
  console.log(`  rooftop:     ${world.rooms['rooftop'].temp.toFixed(1)} C  humidity:${world.rooms['rooftop'].humidity.toFixed(0)}%`)
}

// ══════════════════════════════════════
// ── Main
// ══════════════════════════════════════

async function main() {
  console.log('')
  console.log('  ╔═══════════════════════════════════════════════╗')
  console.log('  ║   H.E.X. — Live Simulation              ║')
  console.log('  ║   Building events unfold. HEX reacts.       ║')
  console.log('  ╚═══════════════════════════════════════════════╝')
  console.log('')

  process.stdout.write('  Booting...')
  const { alive, enrolled } = await setup()
  console.log(` ${alive}/10 edges, ${enrolled}/10 enrolled`)
  await new Promise(r => setTimeout(r, 2000))

  console.log('\n  Simulation starting. Events will unfold automatically.')
  console.log('  You can type commands anytime. Type "exit" to stop.\n')

  simRunning = true

  // Interactive input
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout, prompt: '  You: ' })

  // Simulation loop
  const simLoop = setInterval(async () => {
    if (!simRunning) return

    const event = simulateTick()
    renderStatus()

    if (event) {
      console.log(`\n  *** EVENT: ${event.event} ***`)
      console.log(`  ${event.message}\n`)

      // HEX reacts autonomously
      process.stdout.write('  HEX: thinking...\r')
      const result = await chat(event.message)
      process.stdout.write('                       \r')

      if (result.result) {
        const lines = result.result.split('\n')
        lines.forEach(l => console.log(`  HEX: ${l}`))
        if (result.steps?.length) console.log(`\n  [${result.steps.length} actions]`)

        // Update world based on HEX actions
        if (result.steps) {
          for (const step of result.steps) {
            if (step.tool === 'set_gpio' && step.input?.target?.includes('server-room')) {
              world.rooms['server-room'].cooling_fan = true
            }
            if (step.tool === 'set_gpio' && step.input?.target?.includes('kitchen')) {
              world.rooms['kitchen'].exhaust_fan = true
            }
            if (step.tool === 'set_gpio' && step.input?.target?.includes('meeting')) {
              world.rooms['meeting'].lights = true
            }
            if (step.tool === 'set_gpio' && step.input?.target?.includes('climate')) {
              world.rooms['climate'].ac_on = true
            }
          }
        }
      } else if (result.error) {
        console.log(`  HEX: (error: ${result.error})`)
      }
      console.log('')
      rl.prompt()
    }
  }, 10000)  // 10 seconds per tick

  rl.prompt()
  rl.on('line', async (line) => {
    const input = line.trim()
    if (!input) { rl.prompt(); return }
    if (input === 'exit') { clearInterval(simLoop); rl.close(); return }
    if (input === 'status') { renderStatus(); rl.prompt(); return }

    process.stdout.write('  HEX: thinking...\r')
    const result = await chat(input)
    process.stdout.write('                       \r')
    if (result.result) {
      result.result.split('\n').forEach(l => console.log(`  HEX: ${l}`))
      if (result.steps?.length) console.log(`  [${result.steps.length} actions]`)
    }
    console.log('')
    rl.prompt()
  })

  rl.on('close', () => { clearInterval(simLoop); shutdown(); process.exit(0) })
}

process.on('SIGINT', () => { shutdown(); process.exit(0) })
main().catch(e => { console.error('Fatal:', e.message); shutdown(); process.exit(1) })
