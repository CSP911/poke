#!/usr/bin/env node
/**
 * POKE ESP32-C3 Bare-Metal Kernel Tests
 *
 * Tests the bare-metal POKE OS running on real ESP32-C3 hardware.
 * No FreeRTOS, no ESP-IDF — pure POKE kernel via Direct Boot.
 *
 * Requires ESP32-C3 connected via USB with poke-esp32c3.bin flashed.
 *
 * Usage: node test/esp32c3-bare.test.js
 *        SERIAL_PORT=/dev/ttyUSB0 node test/esp32c3-bare.test.js
 */

const SERIAL_PORT = process.env.SERIAL_PORT || '/dev/cu.usbmodem101'

let pass = 0, fail = 0, total = 0
const results = []

function assert(condition, name) {
  total++
  if (condition) { pass++; results.push(`  PASS  ${name}`) }
  else { fail++; results.push(`  FAIL  ${name}`) }
}

async function run() {
  const fs = require('fs')
  if (!fs.existsSync(SERIAL_PORT)) {
    console.error(`\n  ESP32-C3 not connected at ${SERIAL_PORT}\n`)
    process.exit(1)
  }

  const { SerialPort } = require('serialport')
  const port = new SerialPort({ path: SERIAL_PORT, baudRate: 115200 })
  await new Promise(r => port.on('open', r))
  await new Promise(r => setTimeout(r, 2000))  // wait for boot

  function buildFrame(cmd, data) {
    const p = data ? Buffer.concat([Buffer.from(cmd), data]) : Buffer.from(cmd)
    const h = Buffer.alloc(8); h.write('POKE', 0); h.writeUInt32LE(p.length, 4)
    return Buffer.concat([h, p])
  }

  function waitResp(timeoutMs) {
    return new Promise((resolve, reject) => {
      let rx = Buffer.alloc(0)
      const timer = setTimeout(() => { port.removeListener('data', handler); reject(new Error('TIMEOUT')) }, timeoutMs || 5000)
      const handler = (d) => {
        rx = Buffer.concat([rx, d])
        const idx = rx.indexOf('RESP')
        if (idx >= 0 && rx.length >= idx + 8) {
          const len = rx.readUInt32LE(idx + 4)
          if (rx.length >= idx + 8 + len) {
            clearTimeout(timer)
            port.removeListener('data', handler)
            resolve(rx.slice(idx + 8, idx + 8 + len).toString())
          }
        }
      }
      port.on('data', handler)
    })
  }

  async function send(cmd, data) {
    const p = waitResp()
    port.write(buildFrame(cmd, data))
    return await p
  }

  console.log(`\nPOKE ESP32-C3 Bare-Metal Tests`)
  console.log(`Port: ${SERIAL_PORT}`)
  console.log('─'.repeat(60))

  // === PING ===
  {
    const r = await send('PING')
    assert(r === 'PONG', 'PING → PONG')
  }

  // === INFO ===
  {
    const r = await send('INFO')
    const info = JSON.parse(r)
    assert(info.status === 'alive', 'INFO: status alive')
    assert(info.arch === 'rv32imc', 'INFO: arch rv32imc')
    assert(info.chip === 'esp32c3', 'INFO: chip esp32c3')
    assert(info.kernel === 'poke-os', 'INFO: kernel poke-os')
    assert(info.bare_metal === true, 'INFO: bare_metal true')
    assert(info.freertos === false, 'INFO: freertos false')
    assert(Array.isArray(info.commands), 'INFO: has commands list')
    assert(info.commands.includes('EXEC'), 'INFO: supports EXEC')
    assert(info.commands.includes('PING'), 'INFO: supports PING')
  }

  // === TEMP ===
  {
    const r = await send('TEMP')
    const temp = JSON.parse(r)
    assert(typeof temp.celsius === 'number' || typeof temp.celsius === 'string', 'TEMP: has celsius')
    assert(temp.virtual === true, 'TEMP: virtual sensor')
  }

  // === GPIO ===
  {
    const r = await send('GPIO')
    const gpio = JSON.parse(r)
    assert(gpio.gpio, 'GPIO: has gpio object')
    assert('0' in gpio.gpio, 'GPIO: has pin 0')
    assert('10' in gpio.gpio, 'GPIO: has pin 10')
  }

  // === GPOS ===
  {
    const r = await send('GPOS', Buffer.from([5, 1]))
    const gpos = JSON.parse(r)
    assert(gpos.pin === 5, 'GPOS: pin 5')
    assert(gpos.value === 1, 'GPOS: value 1')
  }

  // === EXEC: li a0, 42 / ret ===
  {
    const code = Buffer.from([0x13, 0x05, 0xa0, 0x02, 0x67, 0x80, 0x00, 0x00])
    const r = await send('EXEC', code)
    assert(r === 'a0=42', 'EXEC: li a0,42 → a0=42')
  }

  // === EXEC: 3 + 4 = 7 ===
  {
    const code = Buffer.from([
      0x13, 0x05, 0x30, 0x00,  // li a0, 3
      0x93, 0x02, 0x40, 0x00,  // li t0, 4
      0x33, 0x05, 0x55, 0x00,  // add a0, a0, t0
      0x67, 0x80, 0x00, 0x00   // ret
    ])
    const r = await send('EXEC', code)
    assert(r === 'a0=7', 'EXEC: 3+4 = a0=7')
  }

  // === EXEC: 10 * 20 = 200 ===
  {
    const code = Buffer.from([
      0x13, 0x05, 0xa0, 0x00,  // li a0, 10
      0x93, 0x02, 0x40, 0x01,  // li t0, 20
      0x33, 0x05, 0x55, 0x02,  // mul a0, a0, t0
      0x67, 0x80, 0x00, 0x00   // ret
    ])
    const r = await send('EXEC', code)
    assert(r === 'a0=200', 'EXEC: 10*20 = a0=200')
  }

  // === EXEC: 100 - 37 = 63 ===
  {
    const code = Buffer.from([
      0x13, 0x05, 0x40, 0x06,  // li a0, 100
      0x93, 0x02, 0x50, 0x02,  // li t0, 37
      0x33, 0x05, 0x55, 0x40,  // sub a0, a0, t0
      0x67, 0x80, 0x00, 0x00   // ret
    ])
    const r = await send('EXEC', code)
    assert(r === 'a0=63', 'EXEC: 100-37 = a0=63')
  }

  // === EXEC with assembler: fibonacci(10) = 55 ===
  {
    const { compileAssemblyRV } = require('../hub/compiler')
    const bin = await compileAssemblyRV(
      'li a0, 0\nli a1, 1\nli t0, 10\nli t1, 0\n' +
      'loop:\nbeq t1, t0, done\nadd t2, a0, a1\nmv a0, a1\nmv a1, t2\n' +
      'addi t1, t1, 1\nj loop\ndone:\nret'
    )
    assert(bin, 'assembler: fibonacci compiles')
    const p = waitResp()
    port.write(buildFrame('EXEC', bin))
    const r = await p
    assert(r === 'a0=55', 'EXEC: fib(10) = a0=55 (via assembler)')
  }

  // === Verify bare-metal: no RET → rejected ===
  {
    const code = Buffer.from([0x13, 0x05, 0xa0, 0x02])  // li a0, 42 without ret
    const r = await send('EXEC', code)
    assert(r.includes('no RET'), 'EXEC: no RET instruction → rejected')
  }

  // === Verify INFO confirms bare-metal ===
  {
    const r = await send('INFO')
    const info = JSON.parse(r)
    assert(info.transport === 'usb-serial-jtag', 'transport: USB-Serial/JTAG (not UART)')
    assert(info.bare_metal === true && info.freertos === false, 'confirmed: bare-metal, no FreeRTOS')
  }

  port.close()

  // Print results
  console.log('')
  results.forEach(r => console.log(r))
  console.log('─'.repeat(60))
  console.log(`${pass} passed, ${fail} failed, ${total} total\n`)

  process.exit(fail > 0 ? 1 : 0)
}

run().catch(e => { console.error('Fatal:', e); process.exit(1) })
