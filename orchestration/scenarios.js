#!/usr/bin/env node
/**
 * HEX — Complex scenario tests
 *
 * Tests LLM's ability to make multi-step autonomous decisions
 * across 10 QEMU edges in a smart office environment.
 *
 * Usage: node orchestration/scenarios.js
 */

const { spawn, execSync } = require('child_process')
const fs = require('fs')
const path = require('path')
const net = require('net')
const http = require('http')

const ROOT = path.resolve(__dirname, '..')
const config = JSON.parse(fs.readFileSync(path.join(__dirname, 'config.json'), 'utf8'))

require('dotenv').config({ path: path.join(ROOT, '.env') })
const HUB_PORT = parseInt(process.env.PORT) || 3333
const HUB_SECRET = process.env.HUB_SECRET || 'poke-secret'

const RV32_BIN = path.join(ROOT, 'rv32', 'poke-rv32.bin')
const processes = []
let hubProc = null
let passed = 0, failed = 0

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
  return httpPost('/relay', { from, command })
}

// ── Test Runner ──

async function scenario(name, command, checks) {
  process.stdout.write(`  ⏳ ${name}...`)
  try {
    const result = await chat(command)
    const text = result.result || ''
    const steps = result.steps || []
    const tools = steps.map(s => s.tool).filter(Boolean)
    const results = steps.map(s => s.result || '').join(' ')

    let pass = true
    const failures = []

    for (const check of checks) {
      if (check.type === 'response_includes') {
        if (!text.toLowerCase().includes(check.value.toLowerCase())) {
          pass = false; failures.push(`response missing "${check.value}"`)
        }
      } else if (check.type === 'used_tool') {
        if (!tools.includes(check.value)) {
          pass = false; failures.push(`didn't use tool "${check.value}"`)
        }
      } else if (check.type === 'min_steps') {
        if (steps.length < check.value) {
          pass = false; failures.push(`only ${steps.length} steps, need >= ${check.value}`)
        }
      } else if (check.type === 'multi_edge') {
        const edgeSet = new Set(steps.map(s => s.input?.target).filter(Boolean))
        if (edgeSet.size < check.value) {
          pass = false; failures.push(`only ${edgeSet.size} edges, need >= ${check.value}`)
        }
      } else if (check.type === 'result_includes') {
        if (!results.toLowerCase().includes(check.value.toLowerCase())) {
          pass = false; failures.push(`results missing "${check.value}"`)
        }
      }
    }

    if (pass) {
      passed++
      console.log(`\r  ✓ ${name} (${steps.length} steps)`)
    } else {
      failed++
      console.log(`\r  ✗ ${name}`)
      failures.forEach(f => console.log(`      → ${f}`))
    }
  } catch (e) {
    failed++
    console.log(`\r  ✗ ${name} — ${e.message}`)
  }
}

// ── Infrastructure ──

async function setup() {
  // Build
  if (!fs.existsSync(RV32_BIN)) {
    console.log('  Building kernel...')
    execSync(`make -C ${path.join(ROOT, 'rv32')}`, { stdio: 'pipe' })
  }

  // Launch edges
  process.stdout.write('  Booting edges...')
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
  console.log(` ${alive}/10`)

  // Hub
  process.stdout.write('  Starting hub...')
  hubProc = spawn('node', ['-e', `require('dotenv').config({path:'${path.join(ROOT,'.env')}'}); require('${path.join(ROOT,'src','hub.js')}')`], {
    stdio: ['pipe', 'pipe', 'pipe'], cwd: ROOT,
  })
  hubProc.stderr.on('data', () => {})
  hubProc.stdout.on('data', () => {})
  await waitForPort(HUB_PORT, 10000)
  console.log(' ready')

  // Enroll
  process.stdout.write('  Enrolling...')
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
  console.log(` ${ok}/10`)
  // Wait for incubation to settle
  await new Promise(r => setTimeout(r, 2000))
}

function teardown() {
  for (const p of processes) p.kill('SIGKILL')
  if (hubProc) hubProc.kill('SIGKILL')
}

// ── Scenarios ──

async function runScenarios() {
  console.log('\n  HEX Scenario Tests\n')
  const delay = () => new Promise(r => setTimeout(r, 3000))

  // 1. Multi-edge temperature survey
  await scenario(
    'Building-wide temperature check',
    '전체 건물의 온도를 확인하고 요약해줘',
    [
      { type: 'min_steps', value: 5 },
      { type: 'multi_edge', value: 3 },
      { type: 'result_includes', value: 'celsius' },
    ]
  )

  await delay()

  // 2. Targeted action — turn on specific relay
  await scenario(
    'Turn on meeting room lights',
    '회의실 조명 켜줘. GPIO 핀 2번을 HIGH로 설정해.',
    [
      { type: 'used_tool', value: 'set_gpio' },
      { type: 'response_includes', value: '회의' },
    ]
  )

  await delay()

  // 3. Cross-room comparison
  await scenario(
    'Compare indoor vs rooftop temperature',
    '작업실 온도와 옥상 온도를 비교해서 환기가 필요한지 판단해줘',
    [
      { type: 'multi_edge', value: 2 },
      { type: 'result_includes', value: 'celsius' },
    ]
  )

  await delay()

  // 4. Emergency scenario
  await scenario(
    'Kitchen gas alert response',
    '주방에서 가스 센서 값이 급상승했어! 환기팬 켜고 상황 보고해줘.',
    [
      { type: 'used_tool', value: 'set_gpio' },
      { type: 'min_steps', value: 1 },
    ]
  )

  await delay()

  // 5. Energy saving
  await scenario(
    'Night mode — save energy',
    '지금 밤 11시야. 사람이 없는 공간의 릴레이를 전부 꺼서 절전해줘.',
    [
      { type: 'min_steps', value: 3 },
      { type: 'response_includes', value: '절전' },
    ]
  )

  await delay()

  // 6. Compute on edge
  await scenario(
    'Execute computation on edge',
    'workspace-1 엣지에서 15 곱하기 7을 계산해줘. RISC-V 어셈블리로.',
    [
      { type: 'used_tool', value: 'execute_rv' },
      { type: 'response_includes', value: '105' },
    ]
  )

  await delay()

  // 7. Server room monitoring setup
  await scenario(
    'Deploy temperature monitor',
    '서버룸 온도가 30도 넘으면 알려줘. 모니터 설정해줘.',
    [
      { type: 'response_includes', value: '모니터' },
    ]
  )

  await delay()

  // 8. Multi-room status report
  await scenario(
    'Security status check',
    '현관, 주차장, 로비의 모션 센서 상태를 확인하고 보안 상황 보고해줘',
    [
      { type: 'multi_edge', value: 2 },
    ]
  )
}

// ── Main ──

async function main() {
  console.log('\n  ╔═══════════════════════════════════════╗')
  console.log('  ║   HEX Scenario Tests               ║')
  console.log('  ║   Complex LLM Decision Making          ║')
  console.log('  ╚═══════════════════════════════════════╝')

  try {
    await setup()
    await runScenarios()
  } catch (e) {
    console.error('  Setup error:', e.message)
  } finally {
    teardown()
  }

  console.log(`\n  ═══ ${passed} passed, ${failed} failed ═══\n`)
  process.exit(failed > 0 ? 1 : 0)
}

process.on('SIGINT', () => { teardown(); process.exit(0) })
setTimeout(() => { console.log('\n  ABORT: global timeout'); teardown(); process.exit(2) }, 300000)

main()
