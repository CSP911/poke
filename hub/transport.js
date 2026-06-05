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

// ── P2P: relay code from one edge to another (edge → hub → edge) ──
// Hub compiles and sends on behalf of the source edge
function pokeRelay(fromEndpoint, toEndpoint, machineCode, arch) {
  log.info(`[p2p] relaying ${machineCode.length}B: ${fromEndpoint} → ${toEndpoint}`)
  if (arch === 'aarch64') {
    return pokeNodeARM(toEndpoint, machineCode)
  }
  return pokeNode(toEndpoint, machineCode)
}

// ── Stream frames to edge (raw TCP, FRM protocol) ──
// Each frame: "FRM" + width(2 LE) + height(2 LE) + RGB pixels
function streamToEdge(endpoint, frames, fps) {
  return new Promise((resolve, reject) => {
    const net = require('net')
    const url = new URL('/poke', endpoint)
    const delay = Math.floor(1000 / (fps || 10))

    const sock = new net.Socket()
    sock.connect(parseInt(url.port), url.hostname, async () => {
      log.info(`[stream] connected to ${endpoint}, ${frames.length} frames at ${fps}fps`)

      // Send STR header to enter stream mode
      const header = 'POST /poke HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/octet-stream\r\nTransfer-Encoding: chunked\r\n\r\nSTR'
      sock.write(header)

      for (let i = 0; i < frames.length; i++) {
        const frame = frames[i]
        // FRM + width(2 LE) + height(2 LE) + RGB pixels
        const frmHeader = Buffer.alloc(7)
        frmHeader[0] = 0x46; frmHeader[1] = 0x52; frmHeader[2] = 0x4D // "FRM"
        frmHeader.writeUInt16LE(frame.width, 3)
        frmHeader.writeUInt16LE(frame.height, 5)

        const packet = Buffer.concat([frmHeader, frame.pixels])
        sock.write(packet)

        if (i < frames.length - 1) {
          await new Promise(r => setTimeout(r, delay))
        }
      }

      sock.end()
      resolve({ frames_sent: frames.length, fps })
    })

    sock.on('error', e => reject(new PokeTransportError('stream error: ' + e.message, 'STREAM_ERROR')))
    setTimeout(() => { sock.destroy(); resolve({ frames_sent: 0, error: 'timeout' }) }, 30000)
  })
}

// ── Deploy monitor to edge (MON protocol) ──
function deployMonitor(endpoint, monitorBinary, intervalMs, condOp, condVal, hubIp, hubPort) {
  return new Promise((resolve, reject) => {
    const url = new URL('/poke', endpoint)

    // Build MON packet: magic(3) + interval(4) + cond_op(1) + cond_val(4) + hub_ip(4) + hub_port(2) + code_len(2) + code(N)
    const packet = Buffer.alloc(20 + monitorBinary.length)
    packet[0] = 0x4D  // 'M'
    packet[1] = 0x4F  // 'O'
    packet[2] = 0x4E  // 'N'
    packet.writeUInt32LE(intervalMs, 3)
    packet[7] = condOp
    packet.writeUInt32LE(condVal, 8)

    // Parse hub IP string to u32 LE
    const ipParts = hubIp.split('.').map(Number)
    packet[12] = ipParts[0]
    packet[13] = ipParts[1]
    packet[14] = ipParts[2]
    packet[15] = ipParts[3]

    packet.writeUInt16LE(hubPort, 16)
    packet.writeUInt16LE(monitorBinary.length, 18)
    monitorBinary.copy(packet, 20)

    const req = http.request({
      hostname: url.hostname, port: url.port,
      path: '/poke', method: 'POST',
      headers: {
        'Content-Type': 'application/octet-stream',
        'Content-Length': packet.length,
      },
      timeout: 10000,
    }, (res) => {
      let body = ''
      res.on('data', c => body += c)
      res.on('end', () => resolve(body))
    })
    req.on('error', e => reject(new PokeTransportError(e.message, 'MONITOR_DEPLOY_ERROR')))
    req.on('timeout', () => { req.destroy(); reject(new PokeTransportError('timeout', 'TIMEOUT')) })
    req.write(packet)
    req.end()
  })
}

// ── Read edge context store (GET /store) ──
async function readEdgeContext(endpoint) {
  return new Promise((resolve, reject) => {
    http.get(endpoint + '/store', { timeout: 10000 }, (res) => {
      let d = ''; res.on('data', c => d += c)
      res.on('end', () => {
        try {
          const lines = d.trim().split('\n')
          if (lines.length === 0) { resolve({ count: 0, entries: [] }); return }
          // Line 0: count,write_idx,total
          const [count, writeIdx, total] = lines[0].split(',').map(Number)
          // Lines 1+: ts:type:data
          const entries = []
          for (let i = 1; i < lines.length; i++) {
            const parts = lines[i].match(/^(\d+):(\d+):(.*)$/)
            if (parts) {
              entries.push({
                timestamp: parseInt(parts[1]),
                type: parseInt(parts[2]),
                data: parts[3],
              })
            }
          }
          resolve({ count, writeIdx, total, entries })
        } catch (e) { resolve({ count: 0, entries: [], error: e.message }) }
      })
    }).on('error', e => resolve({ count: 0, entries: [], error: e.message }))
  })
}

// ── Write context to edge store (CTX protocol) ──
function writeEdgeContext(endpoint, type, timestamp, data) {
  return new Promise((resolve, reject) => {
    const url = new URL('/poke', endpoint)
    const dataBuf = Buffer.from(data, 'utf8')
    const ctx = Buffer.alloc(8 + dataBuf.length)
    ctx[0] = 0x43; ctx[1] = 0x54; ctx[2] = 0x58  // CTX
    ctx[3] = type
    ctx.writeUInt32LE(timestamp, 4)
    dataBuf.copy(ctx, 8)

    const req = http.request({
      hostname: url.hostname, port: url.port,
      path: '/poke', method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': ctx.length },
      timeout: 5000,
    }, (res) => {
      let body = ''
      res.on('data', c => body += c)
      res.on('end', () => resolve(body.trim()))
    })
    req.on('error', e => reject(new PokeTransportError(e.message, 'CTX_WRITE_ERROR')))
    req.on('timeout', () => { req.destroy(); reject(new PokeTransportError('timeout', 'TIMEOUT')) })
    req.write(ctx)
    req.end()
  })
}

// ── Read PCI device list from edge ──
function readEdgePCI(endpoint) {
  return new Promise((resolve, reject) => {
    http.get(endpoint + '/pci', { timeout: 10000 }, (res) => {
      let d = ''; res.on('data', c => d += c)
      res.on('end', () => {
        try {
          const lines = d.trim().split('\n')
          const count = parseInt(lines[0])
          const devices = []
          for (let i = 1; i < lines.length; i++) {
            const m = lines[i].match(/^([V\d]+),([0-9A-F]{4}):([0-9A-F]{4}),([0-9A-F]{2}):([0-9A-F]{2}),([0-9A-F]+)$/)
            if (m) {
              devices.push({
                slot: m[1],
                vendor: m[2],
                device: m[3],
                class: m[4],
                subclass: m[5],
                bar0: m[6],
                virtual: m[1].startsWith('V'),
              })
            }
          }
          resolve({ count, devices })
        } catch (e) { resolve({ count: 0, devices: [], error: e.message }) }
      })
    }).on('error', e => resolve({ count: 0, devices: [], error: e.message }))
  })
}

// ── Deploy resident binary (RES protocol) ──
function deployResident(endpoint, slot, interval, codeBinary) {
  return new Promise((resolve, reject) => {
    const url = new URL('/poke', endpoint)
    // RES packet: "RES" + slot(1) + interval(4) + code(N)
    const packet = Buffer.alloc(8 + codeBinary.length)
    packet[0] = 0x52; packet[1] = 0x45; packet[2] = 0x53  // RES
    packet[3] = slot
    packet.writeUInt32LE(interval, 4)
    codeBinary.copy(packet, 8)

    const req = http.request({
      hostname: url.hostname, port: url.port,
      path: '/poke', method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': packet.length },
      timeout: 10000,
    }, (res) => {
      let body = ''
      res.on('data', c => body += c)
      res.on('end', () => resolve(body.trim()))
    })
    req.on('error', e => reject(new PokeTransportError(e.message, 'RES_DEPLOY_ERROR')))
    req.on('timeout', () => { req.destroy(); reject(new PokeTransportError('timeout', 'TIMEOUT')) })
    req.write(packet)
    req.end()
  })
}

// ── Shutdown edge (DIE protocol) ──
function shutdownEdge(endpoint) {
  return new Promise((resolve, reject) => {
    const url = new URL('/poke', endpoint)
    const body = Buffer.from('DIE')
    const req = http.request({
      hostname: url.hostname, port: url.port,
      path: '/poke', method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': body.length },
      timeout: 5000,
    }, (res) => {
      let data = ''
      res.on('data', c => data += c)
      res.on('end', () => resolve(data))
    })
    req.on('error', e => resolve('shutdown sent (connection closed)'))
    req.on('timeout', () => { req.destroy(); resolve('shutdown sent (timeout)') })
    req.write(body)
    req.end()
  })
}

module.exports = {
  pokeNode,
  pokeNodeARM,
  pokeNodeRaw,
  pokeRelay,
  streamToEdge,
  deployMonitor,
  deployResident,
  shutdownEdge,
  readEdgeContext,
  writeEdgeContext,
  readEdgePCI,
  PokeTransportError,
}
