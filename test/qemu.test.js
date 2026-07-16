/**
 * POKE QEMU Integration Tests
 *
 * Boots RV32 and ARM64 kernels in QEMU, sends POKE protocol frames
 * via UART, and verifies RESP frames come back correctly.
 *
 * RV32: protocol over stdio (same UART as console)
 * ARM64: protocol over TCP socket (UART1 on port 8081)
 *
 * Usage: node test/qemu.test.js
 */

const { spawn, execSync } = require('child_process')
const fs = require('fs')
const net = require('net')
const path = require('path')

const ROOT = path.resolve(__dirname, '..')
const RV32_BIN = path.join(ROOT, 'rv32', 'poke-rv32.bin')
const ARM_BIN = path.join(ROOT, 'arm', 'poke-arm.bin')
const PI0W_BIN = path.join(ROOT, 'pi0w', 'poke-pi0w-qemu.bin')

/* ── Auto-build binaries if missing ── */
function ensureBinary(binPath, makeDir, makeTarget) {
  if (fs.existsSync(binPath)) return true
  console.log(`  BUILD  ${path.basename(binPath)} not found, building...`)
  try {
    execSync(`make -C ${makeDir} ${makeTarget || ''}`, { stdio: 'pipe' })
    return fs.existsSync(binPath)
  } catch (e) {
    console.log(`  SKIP  build failed: ${e.message.split('\n')[0]}`)
    return false
  }
}

/* ── Check QEMU availability ── */
function hasQemu(cmd) {
  try { execSync(`which ${cmd}`, { stdio: 'pipe' }); return true }
  catch { return false }
}

const BOOT_TIMEOUT = 5000
const RESP_TIMEOUT = 3000
const QEMU_KILL_TIMEOUT = 2000

let passed = 0, failed = 0, total = 0

/* ── Helpers ── */

function makePoke(payload) {
  const header = Buffer.alloc(8)
  header.write('POKE', 0)
  header.writeUInt32LE(payload.length, 4)
  return Buffer.concat([header, payload])
}

function parseResp(buf) {
  if (buf.length < 8) return null
  const magic = buf.slice(0, 4).toString()
  if (magic !== 'RESP') return null
  const len = buf.readUInt32LE(4)
  if (buf.length < 8 + len) return null
  return { payload: buf.slice(8, 8 + len), totalLen: 8 + len }
}

function findRespInBuffer(buf) {
  // Scan for 'RESP' magic in mixed console+binary output
  for (let i = 0; i <= buf.length - 8; i++) {
    if (buf[i] === 0x52 && buf[i+1] === 0x45 && buf[i+2] === 0x53 && buf[i+3] === 0x50) {
      const remaining = buf.slice(i)
      const resp = parseResp(remaining)
      if (resp) return resp
    }
  }
  return null
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms))
}

/* ── QEMU Process Manager ── */

class QemuProcess {
  constructor(name, args) {
    this.name = name
    this.args = args
    this.proc = null
    this.stdout = Buffer.alloc(0)
    this.killed = false
  }

  start() {
    return new Promise((resolve, reject) => {
      this.proc = spawn(this.args[0], this.args.slice(1), {
        stdio: ['pipe', 'pipe', 'pipe'],
      })

      this.proc.stdout.on('data', chunk => {
        this.stdout = Buffer.concat([this.stdout, chunk])
      })

      this.proc.stderr.on('data', chunk => {
        // Absorb QEMU stderr warnings
      })

      this.proc.on('error', err => {
        if (!this.killed) reject(err)
      })

      this.proc.on('exit', (code) => {
        if (!this.killed) {
          // Unexpected exit
        }
      })

      // Wait for boot banner
      const bootWait = setTimeout(() => {
        reject(new Error(`${this.name}: boot timeout`))
      }, BOOT_TIMEOUT)

      const checkBoot = setInterval(() => {
        const text = this.stdout.toString('utf8', 0, Math.min(this.stdout.length, 4096))
        if (text.includes('poke-')) {
          clearInterval(checkBoot)
          clearTimeout(bootWait)
          resolve()
        }
      }, 100)
    })
  }

  sendRaw(data) {
    if (this.proc && this.proc.stdin.writable) {
      this.proc.stdin.write(data)
    }
  }

  clearBuffer() {
    this.stdout = Buffer.alloc(0)
  }

  waitForResp(timeoutMs) {
    return new Promise((resolve, reject) => {
      const deadline = setTimeout(() => {
        reject(new Error(`${this.name}: RESP timeout`))
      }, timeoutMs || RESP_TIMEOUT)

      const check = setInterval(() => {
        const resp = findRespInBuffer(this.stdout)
        if (resp) {
          clearInterval(check)
          clearTimeout(deadline)
          // Remove consumed bytes up to and including the response
          const idx = this.stdout.indexOf(Buffer.from('RESP'))
          if (idx >= 0) {
            this.stdout = this.stdout.slice(idx + resp.totalLen)
          }
          resolve(resp.payload)
        }
      }, 50)
    })
  }

  kill() {
    this.killed = true
    if (this.proc) {
      this.proc.stdin.end()
      this.proc.kill('SIGKILL')
      this.proc = null
    }
  }
}

/* ── ARM64 TCP Protocol Client ── */

class ArmTcpClient {
  constructor(port) {
    this.port = port
    this.sock = null
    this.buf = Buffer.alloc(0)
  }

  connect(timeoutMs) {
    return new Promise((resolve, reject) => {
      const deadline = setTimeout(() => {
        reject(new Error('ARM TCP connect timeout'))
      }, timeoutMs || 5000)

      const tryConnect = () => {
        const sock = new net.Socket()
        sock.on('error', () => {
          sock.destroy()
          setTimeout(tryConnect, 200)
        })
        sock.connect(this.port, '127.0.0.1', () => {
          clearTimeout(deadline)
          this.sock = sock
          sock.on('data', chunk => {
            this.buf = Buffer.concat([this.buf, chunk])
          })
          resolve()
        })
      }
      tryConnect()
    })
  }

  send(data) {
    if (this.sock) this.sock.write(data)
  }

  clearBuffer() {
    this.buf = Buffer.alloc(0)
  }

  waitForResp(timeoutMs) {
    return new Promise((resolve, reject) => {
      const deadline = setTimeout(() => {
        reject(new Error('ARM TCP RESP timeout'))
      }, timeoutMs || RESP_TIMEOUT)

      const check = setInterval(() => {
        const resp = findRespInBuffer(this.buf)
        if (resp) {
          clearInterval(check)
          clearTimeout(deadline)
          const idx = this.buf.indexOf(Buffer.from('RESP'))
          if (idx >= 0) {
            this.buf = this.buf.slice(idx + resp.totalLen)
          }
          resolve(resp.payload)
        }
      }, 50)
    })
  }

  close() {
    if (this.sock) {
      this.sock.destroy()
      this.sock = null
    }
  }
}

/* ── Test Runner ── */

async function test(name, fn) {
  total++
  try {
    await fn()
    passed++
    console.log(`  PASS  ${name}`)
  } catch (e) {
    failed++
    console.log(`  FAIL  ${name}: ${e.message}`)
  }
}

function eq(a, b, msg) {
  if (a !== b) throw new Error(`${msg || ''} expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`)
}

function includes(str, sub, msg) {
  if (!str.includes(sub)) throw new Error(`${msg || ''} expected to include ${JSON.stringify(sub)}, got ${JSON.stringify(str)}`)
}

/* ── RV32 Tests ── */

async function testRV32() {
  console.log('\n  RV32 QEMU Tests\n')

  if (!hasQemu('qemu-system-riscv32')) { console.log('  SKIP  qemu-system-riscv32 not found'); return }
  if (!ensureBinary(RV32_BIN, path.join(ROOT, 'rv32'))) return

  const qemu = new QemuProcess('rv32', [
    'qemu-system-riscv32',
    '-machine', 'virt', '-cpu', 'rv32', '-m', '128M',
    '-kernel', RV32_BIN,
    '-nographic', '-bios', 'none',
  ])

  try {
    await qemu.start()
    // Small delay after boot for the shell prompt to settle
    await sleep(300)

    // -- PING
    await test('rv32: PING -> PONG', async () => {
      qemu.clearBuffer()
      const frame = makePoke(Buffer.from('PING'))
      qemu.sendRaw(frame)
      const resp = await qemu.waitForResp()
      eq(resp.toString(), 'PONG')
    })

    // -- INFO
    await test('rv32: INFO -> JSON with arch', async () => {
      qemu.clearBuffer()
      await sleep(100)
      qemu.sendRaw(makePoke(Buffer.from('INFO')))
      const resp = await qemu.waitForResp()
      const json = resp.toString()
      includes(json, '"arch"')
      includes(json, 'rv32im')
      includes(json, '"status":"alive"')
      // Verify it parses as JSON
      JSON.parse(json)
    })

    // -- EXEC (li a0, 42 ; ret)
    await test('rv32: EXEC -> return value 42', async () => {
      qemu.clearBuffer()
      await sleep(100)
      // RV32 machine code:
      //   li a0, 42   => addi a0, x0, 42  => imm=42, rs1=0, rd=10, opcode=0x13
      //     42 = 0x2A => 0x02A00513
      //   ret         => jalr x0, x1, 0   => 0x00008067
      const code = Buffer.from([
        0x13, 0x05, 0xA0, 0x02,  // addi a0, x0, 42
        0x67, 0x80, 0x00, 0x00,  // ret
      ])
      const payload = Buffer.concat([Buffer.from('EXEC'), code])
      qemu.sendRaw(makePoke(payload))
      const resp = await qemu.waitForResp()
      eq(resp.toString(), 'a0=42')
    })

    // -- EXEC (2+3=5)
    await test('rv32: EXEC -> 2+3=5', async () => {
      qemu.clearBuffer()
      await sleep(100)
      // li a0, 2  => addi a0, x0, 2  => 0x00200513
      // addi a0, a0, 3              => 0x00350513
      // ret                          => 0x00008067
      const code = Buffer.from([
        0x13, 0x05, 0x20, 0x00,  // addi a0, x0, 2
        0x13, 0x05, 0x35, 0x00,  // addi a0, a0, 3
        0x67, 0x80, 0x00, 0x00,  // ret
      ])
      const payload = Buffer.concat([Buffer.from('EXEC'), code])
      qemu.sendRaw(makePoke(payload))
      const resp = await qemu.waitForResp()
      eq(resp.toString(), 'a0=5')
    })

    // -- GPIO
    await test('rv32: GPIO -> pin states', async () => {
      qemu.clearBuffer()
      await sleep(100)
      qemu.sendRaw(makePoke(Buffer.from('GPIO')))
      const resp = await qemu.waitForResp()
      const json = resp.toString()
      includes(json, 'gpio')
      JSON.parse(json)
    })

    // -- TEMP
    await test('rv32: TEMP -> temperature', async () => {
      qemu.clearBuffer()
      await sleep(100)
      qemu.sendRaw(makePoke(Buffer.from('TEMP')))
      const resp = await qemu.waitForResp()
      const json = resp.toString()
      includes(json, 'celsius')
      JSON.parse(json)
    })

  } finally {
    qemu.kill()
  }
}

/* ── ARM64 Tests ── */

async function testARM64() {
  console.log('\n  ARM64 QEMU Tests\n')

  if (!hasQemu('qemu-system-aarch64')) { console.log('  SKIP  qemu-system-aarch64 not found'); return }
  if (!ensureBinary(ARM_BIN, path.join(ROOT, 'arm'))) return

  // Use a random port to avoid conflicts
  const port = 18081 + Math.floor(Math.random() * 1000)

  const qemu = new QemuProcess('arm64', [
    'qemu-system-aarch64',
    '-machine', 'virt', '-cpu', 'cortex-a53', '-m', '128M',
    '-kernel', ARM_BIN, '-nographic',
    '-chardev', 'stdio,id=con', '-serial', 'chardev:con',
    '-chardev', `socket,id=net,host=127.0.0.1,port=${port},server=on,wait=off`,
    '-serial', 'chardev:net',
    '-chardev', 'null,id=audio', '-serial', 'chardev:audio',
    '-monitor', 'none',
  ])

  const client = new ArmTcpClient(port)

  try {
    await qemu.start()
    await sleep(500)
    await client.connect(5000)
    await sleep(300)

    // -- PING
    await test('arm64: PING -> PONG', async () => {
      client.clearBuffer()
      client.send(makePoke(Buffer.from('PING')))
      const resp = await client.waitForResp()
      eq(resp.toString(), 'PONG')
    })

    // -- INFO
    await test('arm64: INFO -> JSON with arch', async () => {
      client.clearBuffer()
      await sleep(100)
      client.send(makePoke(Buffer.from('INFO')))
      const resp = await client.waitForResp()
      const json = resp.toString()
      includes(json, '"arch"')
      includes(json, 'aarch64')
      includes(json, '"status":"alive"')
      JSON.parse(json)
    })

    // -- EXEC (mov x0, #42 ; ret)
    await test('arm64: EXEC -> return value 42', async () => {
      client.clearBuffer()
      await sleep(100)
      // ARM64 machine code:
      //   mov x0, #42   => 0xD2800540  (movz x0, #42)
      //   ret            => 0xD65F03C0
      const code = Buffer.from([
        0x40, 0x05, 0x80, 0xD2,  // mov x0, #42
        0xC0, 0x03, 0x5F, 0xD6,  // ret
      ])
      const payload = Buffer.concat([Buffer.from('EXEC'), code])
      client.send(makePoke(payload))
      const resp = await client.waitForResp()
      const text = resp.toString()
      includes(text, '42')
    })

    // -- EXEC (2+3=5)
    await test('arm64: EXEC -> 2+3=5', async () => {
      client.clearBuffer()
      await sleep(100)
      // mov x0, #2  => 0xD2800040
      // add x0, x0, #3 => 0x91000C00
      // ret => 0xD65F03C0
      const code = Buffer.from([
        0x40, 0x00, 0x80, 0xD2,  // mov x0, #2
        0x00, 0x0C, 0x00, 0x91,  // add x0, x0, #3
        0xC0, 0x03, 0x5F, 0xD6,  // ret
      ])
      const payload = Buffer.concat([Buffer.from('EXEC'), code])
      client.send(makePoke(payload))
      const resp = await client.waitForResp()
      const text = resp.toString()
      includes(text, '5')
    })

    // -- GPIO
    await test('arm64: GPIO -> pin states', async () => {
      client.clearBuffer()
      await sleep(100)
      client.send(makePoke(Buffer.from('GPIO')))
      const resp = await client.waitForResp()
      const json = resp.toString()
      includes(json, 'pins')
      JSON.parse(json)
    })

    // -- TEMP
    await test('arm64: TEMP -> temperature', async () => {
      client.clearBuffer()
      await sleep(100)
      client.send(makePoke(Buffer.from('TEMP')))
      const resp = await client.waitForResp()
      const json = resp.toString()
      includes(json, 'temp')
      JSON.parse(json)
    })

  } finally {
    client.close()
    qemu.kill()
  }
}

/* ── ARMv6 (Pi Zero W) Tests ── */

async function testARMv6() {
  console.log('\n  ARMv6 (Pi Zero W) QEMU Tests\n')

  if (!hasQemu('qemu-system-arm')) { console.log('  SKIP  qemu-system-arm not found'); return }
  if (!ensureBinary(PI0W_BIN, path.join(ROOT, 'pi0w'), 'qemu')) return

  const qemu = new QemuProcess('armv6', [
    'qemu-system-arm',
    '-M', 'virt', '-cpu', 'cortex-a7', '-m', '128M',
    '-kernel', PI0W_BIN,
    '-display', 'none',
    '-chardev', 'stdio,id=s0',
    '-serial', 'chardev:s0',
    '-monitor', 'none',
  ])

  try {
    await qemu.start()
    await sleep(300)

    // -- PING
    await test('armv6: PING -> PONG', async () => {
      qemu.clearBuffer()
      qemu.sendRaw(makePoke(Buffer.from('PING')))
      const resp = await qemu.waitForResp()
      eq(resp.toString(), 'PONG')
    })

    // -- INFO
    await test('armv6: INFO -> armv6/bcm2835', async () => {
      qemu.clearBuffer()
      await sleep(100)
      qemu.sendRaw(makePoke(Buffer.from('INFO')))
      const resp = await qemu.waitForResp()
      const json = resp.toString()
      includes(json, '"arch":"armv6"')
      includes(json, '"chip":"bcm2835"')
      includes(json, '"bare_metal":true')
      JSON.parse(json)
    })

    // -- EXEC (mov r0, #42; bx lr) using our assembler
    await test('armv6: EXEC -> r0=42', async () => {
      qemu.clearBuffer()
      await sleep(100)
      const { assembleARMv6 } = require(path.join(ROOT, 'src', 'asm_armv6.js'))
      const bin = assembleARMv6('mov r0, #42\nbx lr')
      const payload = Buffer.concat([Buffer.from('EXEC'), bin])
      qemu.sendRaw(makePoke(payload))
      const resp = await qemu.waitForResp()
      eq(resp.toString(), 'r0=42')
    })

    // -- EXEC (2+3=5)
    await test('armv6: EXEC -> 2+3=5', async () => {
      qemu.clearBuffer()
      await sleep(100)
      const { assembleARMv6 } = require(path.join(ROOT, 'src', 'asm_armv6.js'))
      const bin = assembleARMv6('mov r0, #2\nadd r0, r0, #3\nbx lr')
      const payload = Buffer.concat([Buffer.from('EXEC'), bin])
      qemu.sendRaw(makePoke(payload))
      const resp = await qemu.waitForResp()
      eq(resp.toString(), 'r0=5')
    })

    // -- EXEC (10 * 7 = 70)
    await test('armv6: EXEC -> 10*7=70', async () => {
      qemu.clearBuffer()
      await sleep(100)
      const { assembleARMv6 } = require(path.join(ROOT, 'src', 'asm_armv6.js'))
      const bin = assembleARMv6('mov r0, #10\nmov r1, #7\nmul r0, r0, r1\nbx lr')
      const payload = Buffer.concat([Buffer.from('EXEC'), bin])
      qemu.sendRaw(makePoke(payload))
      const resp = await qemu.waitForResp()
      eq(resp.toString(), 'r0=70')
    })

    // -- GPIO
    await test('armv6: GPIO -> pin states', async () => {
      qemu.clearBuffer()
      await sleep(100)
      qemu.sendRaw(makePoke(Buffer.from('GPIO')))
      const resp = await qemu.waitForResp()
      const json = resp.toString()
      includes(json, 'gpio')
      JSON.parse(json)
    })

    // -- TEMP
    await test('armv6: TEMP -> temperature', async () => {
      qemu.clearBuffer()
      await sleep(100)
      qemu.sendRaw(makePoke(Buffer.from('TEMP')))
      const resp = await qemu.waitForResp()
      const json = resp.toString()
      includes(json, 'celsius')
      JSON.parse(json)
    })

    // -- GPOS (set pin)
    await test('armv6: GPOS -> set GPIO pin', async () => {
      qemu.clearBuffer()
      await sleep(100)
      const payload = Buffer.concat([Buffer.from('GPOS'), Buffer.from([3, 1])])
      qemu.sendRaw(makePoke(payload))
      const resp = await qemu.waitForResp()
      const json = resp.toString()
      includes(json, '"pin":3')
      includes(json, '"value":1')
      JSON.parse(json)
    })

  } finally {
    qemu.kill()
  }
}

/* ── Main ── */

async function main() {
  console.log('\n  POKE QEMU Integration Tests\n')

  try {
    await testRV32()
  } catch (e) {
    console.log(`  ERROR  RV32 suite: ${e.message}`)
  }

  try {
    await testARM64()
  } catch (e) {
    console.log(`  ERROR  ARM64 suite: ${e.message}`)
  }

  try {
    await testARMv6()
  } catch (e) {
    console.log(`  ERROR  ARMv6 suite: ${e.message}`)
  }

  console.log(`\n  ${passed} passed, ${failed} failed, ${total} total\n`)
  process.exit(failed > 0 ? 1 : 0)
}

// Global timeout — kill everything after 60 seconds
const globalTimeout = setTimeout(() => {
  console.error('\n  ABORT  global timeout (60s)\n')
  process.exit(2)
}, 60000)
globalTimeout.unref()

main()
