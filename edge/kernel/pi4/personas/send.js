#!/usr/bin/env node
/* Inject a persona binary into a POKE Pi 4 edge over UDP.
 *
 *   node send.js clock.bin      → set wall clock, then PRUN the binary
 *   node send.js stop           → PSTP (discard current persona)
 */
const dgram = require('dgram')
const fs = require('fs')
const path = require('path')

const HOST = process.env.POKE_HOST || '10.0.0.2'
const PORT = 5555
const TZ_OFFSET_MS = 9 * 3600 * 1000   /* KST — kernel clock() returns local time */

function pokeFrame(payload) {
  const pkt = Buffer.alloc(8 + payload.length)
  pkt.write('POKE', 0)
  pkt.writeUInt32LE(payload.length, 4)
  payload.copy(pkt, 8)
  return pkt
}

function send(sock, payload) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timeout')), 3000)
    sock.once('message', (m) => {
      clearTimeout(timer)
      resolve(m.slice(8, 8 + m.readUInt32LE(4)).toString())
    })
    sock.send(pokeFrame(payload), PORT, HOST)
  })
}

async function main() {
  const arg = process.argv[2]
  if (!arg) { console.error('usage: send.js <persona.bin>|stop'); process.exit(1) }

  const sock = dgram.createSocket('udp4')
  try {
    if (arg === 'stop') {
      console.log('PSTP →', await send(sock, Buffer.from('PSTP')))
      return
    }

    const bin = fs.readFileSync(path.resolve(__dirname, arg))
    console.log(`persona: ${arg} (${bin.length} bytes)`)
    if (bin.length > 1400) throw new Error('binary too large for one UDP frame')

    /* 1. inject wall-clock time (local) */
    const time = Buffer.alloc(12)
    time.write('TIME', 0)
    time.writeBigUInt64LE(BigInt(Date.now() + TZ_OFFSET_MS), 4)
    console.log('TIME →', await send(sock, time))

    /* 2. inject + start the persona (previous one is discarded) */
    console.log('PRUN →', await send(sock, Buffer.concat([Buffer.from('PRUN'), bin])))
  } finally {
    sock.close()
  }
}

main().catch((e) => { console.error('error:', e.message); process.exit(1) })
