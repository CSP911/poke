#!/usr/bin/env node
/**
 * POKE Integration Test Suite
 *
 * Tests the full pipeline: QEMU boot → kernel → hub → all features.
 * These tests MUST pass before any release.
 *
 * Usage:
 *   node test/integration.test.js          (uses existing edge at :8080)
 *   EDGE_URL=http://host:port node test/integration.test.js
 *
 * Prerequisites:
 *   - Docker running
 *   - poke-edge image built (docker build -f Dockerfile.edge -t poke-edge .)
 */

const http = require('http')
const net = require('net')
const { execSync } = require('child_process')

const EDGE_URL = process.env.EDGE_URL || 'http://localhost:8080'
const HUB_PORT = 3334  // dedicated test port
const CONTAINER = 'poke-test-edge'
const TIMEOUT = 10000

let passed = 0, failed = 0, total = 0
const results = []

function test(name, fn) {
  return { name, fn }
}

function httpGet(url, timeout = TIMEOUT) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout }, (res) => {
      let d = ''; res.on('data', c => d += c); res.on('end', () => resolve(d))
    })
    req.on('error', reject)
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')) })
  })
}

function httpPost(url, body, timeout = TIMEOUT) {
  return new Promise((resolve, reject) => {
    const u = new URL(url)
    const req = http.request({
      hostname: u.hostname, port: u.port, path: u.pathname,
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

function rawTcp(host, port, data, timeout = 5000) {
  return new Promise((resolve, reject) => {
    const s = new net.Socket()
    s.connect(port, host, () => { s.write(data) })
    let r = Buffer.alloc(0)
    s.on('data', c => { r = Buffer.concat([r, c]) })
    s.on('close', () => resolve(r.toString()))
    s.on('error', reject)
    setTimeout(() => { s.destroy(); resolve(r.toString()) }, timeout)
  })
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }

function assert(condition, msg) {
  if (!condition) throw new Error(msg || 'assertion failed')
}

// ── Test Definitions ──

const tests = [

  // === Environment ===
  test('Docker is running', async () => {
    const out = execSync('docker info --format "{{.ServerVersion}}"', { timeout: 5000 }).toString().trim()
    assert(out.length > 0, 'docker not responding')
  }),

  test('poke-edge image exists', async () => {
    const out = execSync('docker images poke-edge --format "{{.Repository}}"', { timeout: 5000 }).toString().trim()
    assert(out === 'poke-edge', 'poke-edge image not built')
  }),

  test('QEMU edge boots and responds', async () => {
    // Clean: remove test container AND any other container on port 8080
    try { execSync(`docker rm -f ${CONTAINER} 2>/dev/null`) } catch(e) {}
    try { execSync(`docker rm -f poke-edge-test 2>/dev/null`) } catch(e) {}
    try { execSync(`docker rm -f $(docker ps -q --filter publish=8080) 2>/dev/null`) } catch(e) {}
    await sleep(1000)
    execSync(`docker run -d --name ${CONTAINER} -p 8080:8080 poke-edge`, { timeout: 10000 })
    await sleep(12000)
    const health = await httpGet(EDGE_URL + '/health')
    assert(health.includes('"status":"alive"'), 'health not alive')
  }),

  // === Kernel Core ===
  test('GET /health returns valid JSON', async () => {
    const d = JSON.parse(await httpGet(EDGE_URL + '/health'))
    assert(d.status === 'alive')
    assert(d.arch === 'i386')
    assert(typeof d.ctx === 'number')
  }),

  test('Code execution: mov eax, 42', async () => {
    const { compileAssembly } = require('../hub/compiler')
    const { pokeNode } = require('../hub/transport')
    const bin = await compileAssembly('BITS 32\nmov eax, 42\nret')
    const r = await pokeNode(EDGE_URL, bin)
    assert(r.includes('eax=42'), 'expected eax=42, got: ' + r)
  }),

  test('Code execution: 100 * 7 = 700', async () => {
    const { compileAssembly } = require('../hub/compiler')
    const { pokeNode } = require('../hub/transport')
    const bin = await compileAssembly('BITS 32\nmov eax, 100\nimul eax, eax, 7\nret')
    const r = await pokeNode(EDGE_URL, bin)
    assert(r.includes('eax=700'), 'expected eax=700, got: ' + r)
  }),

  test('Guard rail: rejects HLT', async () => {
    const { compileAssembly } = require('../hub/compiler')
    const bin = await compileAssembly('BITS 32\nhlt')
    assert(bin === null, 'HLT should be rejected')
  }),

  // === PCI Scan ===
  test('GET /pci returns devices', async () => {
    const data = await httpGet(EDGE_URL + '/pci')
    const lines = data.trim().split('\n')
    const count = parseInt(lines[0])
    assert(count >= 8, 'expected at least 8 devices, got ' + count)
    assert(data.includes('8086:100E'), 'missing e1000 NIC')
    assert(data.includes('504B:0001'), 'missing virtual temp sensor')
  }),

  // === Context Store ===
  test('CTX write: stores data on disk', async () => {
    const d = 'integration-test-' + Date.now()
    const ctx = Buffer.alloc(8 + d.length)
    ctx[0] = 0x43; ctx[1] = 0x54; ctx[2] = 0x58; ctx[3] = 1
    ctx.writeUInt32LE(Math.floor(Date.now() / 1000), 4)
    Buffer.from(d).copy(ctx, 8)
    const h = `POST /poke HTTP/1.1\r\nHost:x\r\nContent-Length:${ctx.length}\r\n\r\n`
    const r = await rawTcp('localhost', 8080, Buffer.concat([Buffer.from(h), ctx]))
    assert(r.includes('stored'), 'CTX write failed: ' + r.slice(0, 80))
  }),

  test('GET /store returns entries', async () => {
    const data = await httpGet(EDGE_URL + '/store')
    const lines = data.trim().split('\n')
    assert(lines.length >= 2, 'expected header + entries')
    const count = parseInt(lines[0].split(',')[0])
    assert(count > 0, 'expected entries > 0')
  }),

  test('Persistence: data survives reboot', async () => {
    const before = JSON.parse(await httpGet(EDGE_URL + '/health'))
    const ctxBefore = before.ctx
    assert(ctxBefore > 0, 'need entries before reboot test')

    execSync(`docker restart ${CONTAINER}`, { timeout: 30000 })
    await sleep(15000)

    const after = JSON.parse(await httpGet(EDGE_URL + '/health'))
    assert(after.ctx === ctxBefore, `ctx changed: ${ctxBefore} → ${after.ctx}`)
  }),

  // === Monitor ===
  test('Monitor deploy: MON protocol', async () => {
    const { compileAssembly } = require('../hub/compiler')
    const { deployMonitor } = require('../hub/transport')
    const bin = await compileAssembly('BITS 32\nmov eax, [0x200000]\nret')
    const r = await deployMonitor(EDGE_URL, bin, 5000, 0, 30, '10.0.2.2', 9000)
    assert(r.includes('monitor='), 'deploy failed: ' + r)
  }),

  // === Assembler ===
  test('asm.js: x86 assembler (45 built-in tests)', async () => {
    // Run asm test
    execSync('node test/asm.test.js', { timeout: 10000 })
  }),

  test('asm_arm.js: ARM64 assembler (49 built-in tests)', async () => {
    execSync('node test/asm_arm.test.js', { timeout: 10000 })
  }),

  // === Scenario System (uses direct edge calls, no hub needed) ===
  test('Scenario: virtual registers readable', async () => {
    const h = JSON.parse(await httpGet(EDGE_URL + '/health'))
    assert(typeof h.vr === 'object', 'no virtual registers')
    assert(typeof h.vr.t === 'number', 'no temp register')
    assert(typeof h.vr.c === 'number', 'no cpu register')
    assert(typeof h.vr.p === 'number', 'no power register')
  }),

  test('Scenario: virtual registers writable (reset)', async () => {
    // Write temp=22 via code injection
    const { compileAssembly } = require('../hub/compiler')
    const { pokeNode } = require('../hub/transport')
    const bin = await compileAssembly('BITS 32\nmov dword [0x200000], 22\nmov eax, [0x200000]\nret')
    const r = await pokeNode(EDGE_URL, bin)
    assert(r.includes('eax=22'), 'virt reg write failed: ' + r)
  }),

  test('Incubation: 8+ devices (7 PCI + virtual sensors)', async () => {
    const data = await httpGet(EDGE_URL + '/pci')
    const count = parseInt(data.trim().split('\n')[0])
    assert(count >= 8, 'expected 8+ devices, got ' + count)
    assert(data.includes('504B:0001'), 'missing virtual temp sensor')
    assert(data.includes('8086:100E'), 'missing e1000 NIC')
  }),

  test('ASM cache: sketches loaded', async () => {
    const asmcache = require('../hub/asmcache')
    const list = asmcache.list()
    assert(list.length >= 10, 'expected 10+ templates, got ' + list.length)
    assert(list.some(t => t.key.includes('network')), 'missing network sketch')
    assert(list.some(t => t.key.includes('sensor')), 'missing sensor sketch')
  }),

  test('ASM cache: resolve + compile', async () => {
    const asmcache = require('../hub/asmcache')
    asmcache.register('test_add', 'BITS 32\nmov eax, {{A}}\nadd eax, {{B}}\nret', ['A','B'], 'test', ['test'])
    const bin = await asmcache.resolve('test_add', {A:'10', B:'20'})
    assert(bin && bin.length > 0, 'resolve failed')
    const { pokeNode } = require('../hub/transport')
    const r = await pokeNode(EDGE_URL, bin)
    assert(r.includes('eax=30'), 'cache exec failed: ' + r)
  }),

  // === Resident Binary + Multitasking ===
  test('Resident: deploy to slot 0', async () => {
    const { compileAssembly } = require('../hub/compiler')
    const { deployResident } = require('../hub/transport')
    const bin = await compileAssembly('BITS 32\nmov eax, [0x200000]\nret')
    const r = await deployResident(EDGE_URL, 0, 5000, bin)
    assert(r.includes('resident=0'), 'deploy failed: ' + r)
  }),

  test('Resident: edge still responds during resident execution', async () => {
    await sleep(2000)
    const h = JSON.parse(await httpGet(EDGE_URL + '/health'))
    assert(h.status === 'alive', 'edge unresponsive with resident running')
  }),

  test('Resident: stop slot 0', async () => {
    const { stopResident } = require('../hub/transport')
    const r = await stopResident(EDGE_URL, 0)
    assert(r.includes('stopped=0'), 'stop failed: ' + r)
  }),

  test('Resident: edge still works after stop', async () => {
    const { compileAssembly } = require('../hub/compiler')
    const { pokeNode } = require('../hub/transport')
    const bin = await compileAssembly('BITS 32\nmov eax, 77\nret')
    const r = await pokeNode(EDGE_URL, bin)
    assert(r.includes('eax=77'), 'exec after stop failed: ' + r)
  }),

  // === DIE (shutdown) — run last ===
  test('DIE: graceful QEMU shutdown', async () => {
    const { shutdownEdge } = require('../hub/transport')
    const r = await shutdownEdge(EDGE_URL)
    assert(r.includes('shutdown'), 'DIE failed: ' + r)
    await sleep(5000)
    const status = execSync(`docker ps -a --filter name=${CONTAINER} --format "{{.Status}}"`, { timeout: 5000 }).toString()
    assert(status.includes('Exited'), 'container should be exited: ' + status)
  }),
]

// ── Runner ──

async function run() {
  console.log(`\n  POKE Integration Test Suite\n  ${'═'.repeat(40)}\n`)

  for (const t of tests) {
    total++
    process.stdout.write(`  ${t.name} ... `)
    try {
      await t.fn()
      passed++
      console.log('\x1b[32mPASS\x1b[0m')
      results.push({ name: t.name, status: 'PASS' })
    } catch (e) {
      failed++
      console.log(`\x1b[31mFAIL\x1b[0m  ${e.message}`)
      results.push({ name: t.name, status: 'FAIL', error: e.message })
    }
  }

  console.log(`\n  ${'─'.repeat(40)}`)
  console.log(`  ${passed} passed, ${failed} failed, ${total} total\n`)

  // Cleanup
  try { execSync(`docker rm -f ${CONTAINER} 2>/dev/null`) } catch(e) {}

  process.exit(failed > 0 ? 1 : 0)
}

run().catch(e => { console.error('Fatal:', e.message); process.exit(1) })
