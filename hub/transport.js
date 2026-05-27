/**
 * POKE Hub — transport: pokeNode, pokeNodeARM, pokeNodeRaw
 *
 * All transport functions return proper Error objects on failure
 * instead of plain "error: ..." strings.
 */

const http = require('http')
const { log } = require('./logger')

class PokeTransportError extends Error {
  constructor(message, code) {
    super(message)
    this.name = 'PokeTransportError'
    this.code = code
  }
}

// ── Send binary to POKE node (x86, HTTP) ──
function pokeNode(endpoint, machineCode) {
  return new Promise((resolve, reject) => {
    const url = new URL('/poke', endpoint)
    const options = {
      hostname: url.hostname,
      port: url.port,
      path: '/poke',
      method: 'POST',
      headers: {
        'Content-Type': 'application/octet-stream',
        'Content-Length': machineCode.length,
      },
      timeout: 10000,
    }

    const req = http.request(options, (res) => {
      const chunks = []
      let totalBytes = 0
      const MAX_RESPONSE = 1024 * 1024 // 1MB limit
      res.on('data', chunk => {
        totalBytes += chunk.length
        if (totalBytes > MAX_RESPONSE) {
          req.destroy()
          reject(new PokeTransportError('response too large', 'RESPONSE_TOO_LARGE'))
          return
        }
        chunks.push(chunk)
      })
      res.on('end', () => resolve(Buffer.concat(chunks).toString()))
    })
    req.on('error', e => reject(new PokeTransportError(e.message, 'NETWORK_ERROR')))
    req.on('timeout', () => { req.destroy(); reject(new PokeTransportError('timeout', 'TIMEOUT')) })
    req.write(machineCode)
    req.end()
  })
}

// ── Send raw binary to POKE node (IMG protocol) ──
function pokeNodeRaw(endpoint, body) {
  return new Promise((resolve, reject) => {
    const url = new URL('/poke', endpoint)
    const req = http.request({
      hostname: url.hostname, port: url.port, path: '/poke', method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': body.length },
      timeout: 15000,
    }, (res) => {
      const chunks = []
      let totalBytes = 0
      const MAX_RESPONSE = 1024 * 1024
      res.on('data', c => {
        totalBytes += c.length
        if (totalBytes > MAX_RESPONSE) {
          req.destroy()
          reject(new PokeTransportError('response too large', 'RESPONSE_TOO_LARGE'))
          return
        }
        chunks.push(c)
      })
      res.on('end', () => resolve(Buffer.concat(chunks).toString()))
    })
    req.on('error', e => reject(new PokeTransportError(e.message, 'NETWORK_ERROR')))
    req.on('timeout', () => { req.destroy(); reject(new PokeTransportError('timeout', 'TIMEOUT')) })
    req.write(body); req.end()
  })
}

// ── Send code to ARM POKE node (serial protocol over TCP) ──
function pokeNodeARM(endpoint, machineCode) {
  return new Promise((resolve, reject) => {
    const net = require('net')
    const match = endpoint.match(/tcp:\/\/([^:]+):(\d+)/)
    if (!match) { reject(new PokeTransportError('invalid ARM endpoint', 'INVALID_ENDPOINT')); return }

    const sock = new net.Socket()
    const MAX_RESPONSE = 1024 * 1024

    sock.connect(parseInt(match[2]), match[1], () => {
      const execCmd = Buffer.from('EXEC')
      const payload = Buffer.concat([execCmd, machineCode])

      const header = Buffer.alloc(8)
      header.write('POKE', 0)
      header.writeUInt32LE(payload.length, 4)

      sock.write(Buffer.concat([header, payload]))
    })

    let response = Buffer.alloc(0)
    sock.on('data', (data) => {
      response = Buffer.concat([response, data])
      if (response.length > MAX_RESPONSE) {
        sock.destroy()
        reject(new PokeTransportError('ARM response too large', 'RESPONSE_TOO_LARGE'))
        return
      }
      if (response.length >= 8 && response.slice(0, 4).toString() === 'RESP') {
        const respLen = response.readUInt32LE(4)
        if (response.length >= 8 + respLen) {
          const result = response.slice(8, 8 + respLen).toString()
          sock.destroy()
          resolve(result)
        }
      }
    })

    sock.on('error', (e) => reject(new PokeTransportError('ARM error: ' + e.message, 'ARM_NETWORK_ERROR')))
    setTimeout(() => { sock.destroy(); reject(new PokeTransportError('ARM timeout', 'TIMEOUT')) }, 10000)
  })
}

module.exports = {
  pokeNode,
  pokeNodeARM,
  pokeNodeRaw,
  PokeTransportError,
}
