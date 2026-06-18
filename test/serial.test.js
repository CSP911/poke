#!/usr/bin/env node
/**
 * POKE Serial Integration Test Suite
 *
 * Tests the full pipeline: Hub → RISC-V compile → serial → ESP32-C3 → result.
 * Requires an ESP32-C3 connected via USB with POKE UART firmware.
 *
 * Usage:
 *   node test/serial.test.js
 *   SERIAL_PORT=/dev/ttyUSB0 node test/serial.test.js
 */

const http = require('http')

const SERIAL_PORT = process.env.SERIAL_PORT || '/dev/cu.usbmodem1101'
const HUB_PORT = 3335  // dedicated test port
const SERIAL_ENDPOINT = `serial://${SERIAL_PORT}`
const NODE_ID = 'esp32-c3-test'

let passed = 0, failed = 0, total = 0
const results = []

function test(name, fn) {
  return { name, fn }
}

function httpGet(url, timeout = 10000) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout }, (res) => {
      let d = ''; res.on('data', c => d += c); res.on('end', () => resolve(d))
    })
    req.on('error', reject)
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')) })
  })
}

function httpPost(url, body, timeout = 10000) {
  return new Promise((resolve, reject) => {
    const u = new URL(url)
    const req = http.request({
      hostname: u.hostname, port: u.port, path: u.pathname + (u.search || ''),
      method: 'POST', headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
      timeout,
    }, (res) => {
      let d = ''; res.on('data', c => d += c); res.on('end', () => resolve(d))
    })
    req.on('error', reject)
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')) })
    req.write(body); req.end()
  })
}

function assert(condition, msg) {
  if (!condition) throw new Error('Assertion failed: ' + msg)
}

// ── Tests ──

const tests = [
  // === Phase 1: Serial Module Direct Tests ===
  test('serial: PING → PONG', async () => {
    const { serialPing } = require('../hub/serial')
    const result = await serialPing(SERIAL_ENDPOINT)
    assert(result === 'PONG', `expected PONG, got "${result}"`)
  }),

  test('serial: INFO returns valid JSON', async () => {
    const { serialInfo } = require('../hub/serial')
    const result = await serialInfo(SERIAL_ENDPOINT)
    const info = JSON.parse(result)
    assert(info.status === 'alive', 'status should be alive')
    assert(info.arch === 'riscv32', 'arch should be riscv32')
    assert(info.chip === 'esp32c3', 'chip should be esp32c3')
    assert(info.transport === 'usb-serial', 'transport should be usb-serial')
    assert(typeof info.free_heap === 'number', 'free_heap should be number')
    assert(typeof info.uptime === 'number', 'uptime should be number')
  }),

  test('serial: GPIO returns pin states', async () => {
    const { serialGpio } = require('../hub/serial')
    const result = await serialGpio(SERIAL_ENDPOINT)
    const gpio = JSON.parse(result)
    assert(gpio.gpio, 'should have gpio object')
    assert('0' in gpio.gpio, 'should have pin 0')
  }),

  // === Phase 2: RISC-V Compile + Serial Execute ===
  test('compile+exec: li a0, 42 / ret → a0=42', async () => {
    const { compileAssemblyRV } = require('../hub/compiler')
    const { pokeNodeSerial } = require('../hub/serial')
    const bin = await compileAssemblyRV('li a0, 42\nret')
    assert(bin, 'compilation should succeed')
    const result = await pokeNodeSerial(SERIAL_ENDPOINT, bin)
    assert(result === 'a0=42', `expected "a0=42", got "${result}"`)
  }),

  test('compile+exec: 3 + 4 = 7', async () => {
    const { compileAssemblyRV } = require('../hub/compiler')
    const { pokeNodeSerial } = require('../hub/serial')
    const bin = await compileAssemblyRV('li a0, 3\nli t0, 4\nadd a0, a0, t0\nret')
    const result = await pokeNodeSerial(SERIAL_ENDPOINT, bin)
    assert(result === 'a0=7', `expected "a0=7", got "${result}"`)
  }),

  test('compile+exec: 10 * 20 = 200', async () => {
    const { compileAssemblyRV } = require('../hub/compiler')
    const { pokeNodeSerial } = require('../hub/serial')
    const bin = await compileAssemblyRV('li a0, 10\nli t0, 20\nmul a0, a0, t0\nret')
    const result = await pokeNodeSerial(SERIAL_ENDPOINT, bin)
    assert(result === 'a0=200', `expected "a0=200", got "${result}"`)
  }),

  test('compile+exec: 100 - 37 = 63', async () => {
    const { compileAssemblyRV } = require('../hub/compiler')
    const { pokeNodeSerial } = require('../hub/serial')
    const bin = await compileAssemblyRV('li a0, 100\nli t0, 37\nsub a0, a0, t0\nret')
    const result = await pokeNodeSerial(SERIAL_ENDPOINT, bin)
    assert(result === 'a0=63', `expected "a0=63", got "${result}"`)
  }),

  test('compile+exec: bitwise AND 0xFF & 0xF0 = 240', async () => {
    const { compileAssemblyRV } = require('../hub/compiler')
    const { pokeNodeSerial } = require('../hub/serial')
    const bin = await compileAssemblyRV('li a0, 0xFF\nli t0, 0xF0\nand a0, a0, t0\nret')
    const result = await pokeNodeSerial(SERIAL_ENDPOINT, bin)
    assert(result === 'a0=240', `expected "a0=240", got "${result}"`)
  }),

  test('compile+exec: left shift 1 << 8 = 256', async () => {
    const { compileAssemblyRV } = require('../hub/compiler')
    const { pokeNodeSerial } = require('../hub/serial')
    const bin = await compileAssemblyRV('li a0, 1\nli t0, 8\nsll a0, a0, t0\nret')
    const result = await pokeNodeSerial(SERIAL_ENDPOINT, bin)
    assert(result === 'a0=256', `expected "a0=256", got "${result}"`)
  }),

  test('compile+exec: fibonacci(10) = 55', async () => {
    const { compileAssemblyRV } = require('../hub/compiler')
    const { pokeNodeSerial } = require('../hub/serial')
    const asm = `
      li a0, 0
      li a1, 1
      li t0, 10
      li t1, 0
fib_loop:
      beq t1, t0, fib_done
      add t2, a0, a1
      mv a0, a1
      mv a1, t2
      addi t1, t1, 1
      j fib_loop
fib_done:
      ret
    `
    const bin = await compileAssemblyRV(asm)
    assert(bin, 'fibonacci compilation should succeed')
    const result = await pokeNodeSerial(SERIAL_ENDPOINT, bin)
    assert(result === 'a0=55', `expected "a0=55", got "${result}"`)
  }),

  // === Phase 3: Hub API Pipeline ===
  test('hub: enroll serial edge', async () => {
    const body = JSON.stringify({
      node_id: NODE_ID,
      arch: 'riscv32',
      memory_mb: 4,
      capabilities: ['compute', 'gpio'],
      endpoint: SERIAL_ENDPOINT,
    })
    const result = JSON.parse(await httpPost(`http://localhost:${HUB_PORT}/enroll`, body))
    assert(result.ok === true, 'enroll should succeed')
    assert(result.enrolled === NODE_ID, 'enrolled node id should match')
  }),

  test('hub: /nodes shows serial edge alive', async () => {
    const result = JSON.parse(await httpGet(`http://localhost:${HUB_PORT}/nodes`))
    const node = result.nodes.find(n => n.node_id === NODE_ID)
    assert(node, 'node should exist')
    assert(node.status === 'alive', 'node should be alive')
    assert(node.endpoint === SERIAL_ENDPOINT, 'endpoint should be serial')
    assert(node.arch === 'riscv32', 'arch should be riscv32')
  }),

  test('hub: /serial/health probe returns device info', async () => {
    const result = JSON.parse(await httpGet(`http://localhost:${HUB_PORT}/serial/health/${NODE_ID}`))
    assert(result.status === 'alive', 'should be alive')
    assert(result.chip === 'esp32c3', 'should be esp32c3')
    assert(result.transport === 'usb-serial', 'transport should be usb-serial')
  }),

  test('hub: poke-raw 5+5=10 via serial', async () => {
    // Use the assembler for correct encoding
    const { compileAssemblyRV } = require('../hub/compiler')
    const bin = await compileAssemblyRV('li a0, 5\nli t0, 5\nadd a0, a0, t0\nret')
    const hex = bin.toString('hex')
    const body = JSON.stringify({ node_id: NODE_ID, hex })
    const result = JSON.parse(await httpPost(`http://localhost:${HUB_PORT}/poke-raw`, body))
    assert(result.node === NODE_ID, 'node should match')
    assert(result.result === 'a0=10', `expected "a0=10", got "${result.result}"`)
  }),

  test('hub: poke-raw 7*8=56 via serial', async () => {
    const { compileAssemblyRV } = require('../hub/compiler')
    const bin = await compileAssemblyRV('li a0, 7\nli t0, 8\nmul a0, a0, t0\nret')
    const body = JSON.stringify({ node_id: NODE_ID, hex: bin.toString('hex') })
    const result = JSON.parse(await httpPost(`http://localhost:${HUB_PORT}/poke-raw`, body))
    assert(result.result === 'a0=56', `expected "a0=56", got "${result.result}"`)
  }),

  test('hub: /serial/ports lists USB devices', async () => {
    const result = JSON.parse(await httpGet(`http://localhost:${HUB_PORT}/serial/ports`))
    assert(Array.isArray(result), 'should be array')
  }),
]

// ── Runner ──

async function run() {
  console.log(`\nPOKE Serial Integration Tests`)
  console.log(`Serial port: ${SERIAL_PORT}`)
  console.log(`Hub port: ${HUB_PORT}`)
  console.log('─'.repeat(60))

  // Check serial port exists
  const fs = require('fs')
  if (!fs.existsSync(SERIAL_PORT)) {
    console.error(`\n  ERROR: Serial port ${SERIAL_PORT} not found`)
    console.error('  Connect ESP32-C3 via USB and set SERIAL_PORT if needed.\n')
    process.exit(1)
  }

  // Start hub on test port
  process.env.PORT = String(HUB_PORT)
  process.env.LOG_LEVEL = 'warn'
  const { createServer } = require('../hub/server')
  const { loadProfiles, startHealthCheck } = require('../hub/nodes')
  loadProfiles()
  startHealthCheck()
  const server = createServer()
  await new Promise(r => server.listen(HUB_PORT, r))

  // Run tests
  for (const t of tests) {
    total++
    try {
      await t.fn()
      passed++
      console.log(`  PASS  ${t.name}`)
      results.push({ name: t.name, status: 'pass' })
    } catch (e) {
      failed++
      console.log(`  FAIL  ${t.name}: ${e.message}`)
      results.push({ name: t.name, status: 'fail', error: e.message })
    }
  }

  // Close serial
  try { require('../hub/serial').closeAll() } catch (e) {}

  console.log('─'.repeat(60))
  console.log(`${passed} passed, ${failed} failed, ${total} total\n`)

  // Write test report
  const report = {
    timestamp: new Date().toISOString(),
    serial_port: SERIAL_PORT,
    total, passed, failed,
    results,
  }
  const reportPath = require('path').join(__dirname, '..', 'test-serial-report.json')
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2))
  console.log(`Report saved: ${reportPath}\n`)

  server.close()
  process.exit(failed > 0 ? 1 : 0)
}

run().catch(e => { console.error('Fatal:', e); process.exit(1) })
