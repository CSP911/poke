/**
 * POKE Hub — Serial transport for UART-connected edges
 *
 * Handles serial:///dev/ttyUSB0 (or serial:///dev/cu.usbserial-*) endpoints.
 * Implements the POKE binary frame protocol over UART.
 *
 * Commands are serialized per-port: one request/response at a time.
 * This prevents health checks and user commands from interleaving.
 *
 * Protocol:
 *   Request:  "POKE" (4) + payload_len (4 LE) + payload
 *   Response: "RESP" (4) + payload_len (4 LE) + payload
 *   Event:    "EVNT" (4) + payload_len (4 LE) + JSON  (edge → hub, unsolicited)
 */

const net = require('net')
const dgram = require('dgram')
const { log } = require('./logger')

// ── Event callback: set by hub to handle edge-initiated events ──
let _onEvent = null
function onSerialEvent(callback) {
  _onEvent = callback
}

class PokeTransportError extends Error {
  constructor(message, code) {
    super(message)
    this.name = 'PokeTransportError'
    this.code = code
  }
}

// Lazy-load serialport (optional dependency)
let SerialPort = null
function getSerialPort() {
  if (!SerialPort) {
    try {
      SerialPort = require('serialport').SerialPort
    } catch (e) {
      throw new Error('serialport package not installed. Run: npm install serialport')
    }
  }
  return SerialPort
}

// ── Active serial connections (reuse across requests) ──
const ports = new Map()  // path → { port, lock, onResp }

function parseSerialEndpoint(endpoint) {
  // UDP endpoint: udp://host:port
  const udpMatch = endpoint.match(/^udp:\/\/([^:]+):(\d+)$/)
  if (udpMatch) {
    return { path: `udp:${udpMatch[1]}:${udpMatch[2]}`, host: udpMatch[1], udpPort: parseInt(udpMatch[2]), isUdp: true }
  }
  // TCP endpoint: tcp://host:port
  const tcpMatch = endpoint.match(/^tcp:\/\/([^:]+):(\d+)$/)
  if (tcpMatch) {
    return { path: `tcp:${tcpMatch[1]}:${tcpMatch[2]}`, host: tcpMatch[1], tcpPort: parseInt(tcpMatch[2]), isTcp: true }
  }
  // Serial endpoint: serial:///dev/...
  const match = endpoint.match(/^serial:\/\/(.+?)(\?.*)?$/)
  if (!match) return null
  const portPath = match[1]
  const params = new URLSearchParams(match[2] || '')
  const baud = parseInt(params.get('baud')) || 115200
  return { path: portPath, baud }
}

function getOrOpenTcp(key, host, tcpPort) {
  if (ports.has(key)) {
    const entry = ports.get(key)
    if (!entry.port.destroyed) return entry
    ports.delete(key)
  }

  const sock = new net.Socket()
  // Wrap socket to match serial port API
  sock.isOpen = false
  const entry = { port: sock, lock: Promise.resolve(), onResp: null }

  sock.connect(tcpPort, host, () => {
    sock.isOpen = true
    log.info(`[tcp] connected ${host}:${tcpPort}`)
  })

  sock.on('error', (err) => {
    log.error(`[tcp] ${key} error: ${err.message}`)
    if (entry.onResp) {
      entry.onResp.reject(new PokeTransportError(err.message, 'TCP_ERROR'))
      entry.onResp = null
    }
  })

  sock.on('close', () => {
    sock.isOpen = false
    log.info(`[tcp] ${key} closed`)
    ports.delete(key)
  })

  // Frame parser (same as serial)
  let rxBuf = Buffer.alloc(0)
  sock.on('data', (data) => {
    rxBuf = Buffer.concat([rxBuf, data])
    while (rxBuf.length >= 4) {
      const respIdx = rxBuf.indexOf('RESP')
      const evntIdx = rxBuf.indexOf('EVNT')
      let frameIdx = -1, frameType = null
      if (respIdx >= 0 && (evntIdx < 0 || respIdx <= evntIdx)) { frameIdx = respIdx; frameType = 'RESP' }
      else if (evntIdx >= 0) { frameIdx = evntIdx; frameType = 'EVNT' }
      if (frameIdx < 0) { if (rxBuf.length > 3) rxBuf = rxBuf.slice(-3); break }
      if (frameIdx > 0) rxBuf = rxBuf.slice(frameIdx)
      if (rxBuf.length < 8) break
      const payloadLen = rxBuf.readUInt32LE(4)
      if (payloadLen > 1024 * 1024) { rxBuf = rxBuf.slice(4); continue }
      const frameSize = 8 + payloadLen
      if (rxBuf.length < frameSize) break
      const payload = rxBuf.slice(8, frameSize).toString()
      rxBuf = rxBuf.slice(frameSize)
      if (frameType === 'RESP') {
        if (entry.onResp) {
          const cb = entry.onResp; entry.onResp = null; clearTimeout(cb.timer); cb.resolve(payload)
        }
      } else if (frameType === 'EVNT') {
        log.info(`[tcp] EVNT from ${key}: ${payload.slice(0, 100)}`)
        if (_onEvent) {
          try { const evt = JSON.parse(payload); evt._endpoint = `tcp://${host}:${tcpPort}`; _onEvent(evt) }
          catch (e) { log.warn(`[tcp] EVNT parse error: ${e.message}`) }
        }
      }
    }
  })

  ports.set(key, entry)
  return entry
}

// ── UDP sockets ──
const udpSockets = new Map()

function udpCommand(key, host, port, command, data, timeoutMs) {
  return new Promise((resolve, reject) => {
    let sock = udpSockets.get(key)
    if (!sock) {
      const s = dgram.createSocket('udp4')
      s.on('error', (err) => { log.error(`[udp] ${key}: ${err.message}`) })
      s.bind()
      sock = { socket: s, host, port }
      udpSockets.set(key, sock)
    }

    const frame = buildFrame(command, data)
    const timeout = timeoutMs || 5000

    const timer = setTimeout(() => {
      sock.socket.removeListener('message', onMsg)
      reject(new PokeTransportError('udp timeout', 'TIMEOUT'))
    }, timeout)

    function onMsg(msg) {
      let rxBuf = msg
      while (rxBuf.length >= 8) {
        const respIdx = rxBuf.indexOf('RESP')
        const evntIdx = rxBuf.indexOf('EVNT')
        let fi = -1, ft = null
        if (respIdx >= 0 && (evntIdx < 0 || respIdx <= evntIdx)) { fi = respIdx; ft = 'RESP' }
        else if (evntIdx >= 0) { fi = evntIdx; ft = 'EVNT' }
        if (fi < 0) break
        if (fi > 0) rxBuf = rxBuf.slice(fi)
        if (rxBuf.length < 8) break
        const plen = rxBuf.readUInt32LE(4)
        if (rxBuf.length < 8 + plen) break
        const payload = rxBuf.slice(8, 8 + plen).toString()
        rxBuf = rxBuf.slice(8 + plen)
        if (ft === 'RESP') {
          clearTimeout(timer)
          sock.socket.removeListener('message', onMsg)
          resolve(payload)
          return
        } else if (ft === 'EVNT' && _onEvent) {
          try { const evt = JSON.parse(payload); evt._endpoint = `udp://${host}:${port}`; _onEvent(evt) }
          catch (e) { log.warn(`[udp] EVNT parse error: ${e.message}`) }
        }
      }
    }

    sock.socket.on('message', onMsg)
    sock.socket.send(frame, 0, frame.length, sock.port, sock.host, (err) => {
      if (err) { clearTimeout(timer); sock.socket.removeListener('message', onMsg); reject(new PokeTransportError('udp send: ' + err.message, 'WRITE_ERROR')) }
    })
  })
}

function getOrOpenPort(portPath, baud) {
  if (ports.has(portPath)) {
    const entry = ports.get(portPath)
    if (entry.port.isOpen) return entry
    ports.delete(portPath)
  }

  const SP = getSerialPort()
  const port = new SP({ path: portPath, baudRate: baud, autoOpen: false })

  // lock: promise chain for serializing commands
  // onResp: callback for the single in-flight request
  const entry = { port, lock: Promise.resolve(), onResp: null }

  port.on('open', () => {
    log.info(`[serial] opened ${portPath} @ ${baud} baud`)
  })

  port.on('error', (err) => {
    log.error(`[serial] ${portPath} error: ${err.message}`)
    if (entry.onResp) {
      entry.onResp.reject(new PokeTransportError(err.message, 'SERIAL_ERROR'))
      entry.onResp = null
    }
  })

  port.on('close', () => {
    log.info(`[serial] ${portPath} closed`)
    ports.delete(portPath)
  })

  // Frame parser: accumulate data and extract RESP + EVNT frames
  let rxBuf = Buffer.alloc(0)
  port.on('data', (data) => {
    rxBuf = Buffer.concat([rxBuf, data])

    while (rxBuf.length >= 4) {
      // Find "RESP" or "EVNT" magic — skip any ESP log output mixed in
      const respIdx = rxBuf.indexOf('RESP')
      const evntIdx = rxBuf.indexOf('EVNT')

      // Pick whichever comes first
      let frameIdx = -1
      let frameType = null
      if (respIdx >= 0 && (evntIdx < 0 || respIdx <= evntIdx)) { frameIdx = respIdx; frameType = 'RESP' }
      else if (evntIdx >= 0) { frameIdx = evntIdx; frameType = 'EVNT' }

      if (frameIdx < 0) {
        if (rxBuf.length > 3) rxBuf = rxBuf.slice(-3)
        break
      }
      if (frameIdx > 0) rxBuf = rxBuf.slice(frameIdx)
      if (rxBuf.length < 8) break

      const payloadLen = rxBuf.readUInt32LE(4)
      if (payloadLen > 1024 * 1024) {
        rxBuf = rxBuf.slice(4)
        continue
      }

      const frameSize = 8 + payloadLen
      if (rxBuf.length < frameSize) break

      const payload = rxBuf.slice(8, frameSize).toString()
      rxBuf = rxBuf.slice(frameSize)

      if (frameType === 'RESP') {
        // Resolve the in-flight request
        if (entry.onResp) {
          const cb = entry.onResp
          entry.onResp = null
          clearTimeout(cb.timer)
          cb.resolve(payload)
        }
      } else if (frameType === 'EVNT') {
        // Edge-initiated event — fire callback
        log.info(`[serial] EVNT from ${portPath}: ${payload.slice(0, 100)}`)
        if (_onEvent) {
          try {
            const evt = JSON.parse(payload)
            evt._endpoint = `serial://${portPath}`
            _onEvent(evt)
          } catch (e) {
            log.warn(`[serial] EVNT parse error: ${e.message}`)
          }
        }
      }
    }
  })

  port.open()
  ports.set(portPath, entry)
  return entry
}

// ── Build POKE frame ──
function buildFrame(command, data) {
  const cmdBuf = Buffer.from(command)
  const payload = data ? Buffer.concat([cmdBuf, data]) : cmdBuf
  const header = Buffer.alloc(8)
  header.write('POKE', 0)
  header.writeUInt32LE(payload.length, 4)
  return Buffer.concat([header, payload])
}

// ── Send command and wait for response (serialized per-port) ──
function serialCommand(endpoint, command, data, timeoutMs) {
  const parsed = parseSerialEndpoint(endpoint)
  if (!parsed) {
    return Promise.reject(new PokeTransportError('invalid endpoint: ' + endpoint, 'INVALID_ENDPOINT'))
  }

  // UDP: stateless, handle separately
  if (parsed.isUdp) {
    return udpCommand(parsed.path, parsed.host, parsed.udpPort, command, data, timeoutMs)
  }

  let entry
  try {
    entry = parsed.isTcp
      ? getOrOpenTcp(parsed.path, parsed.host, parsed.tcpPort)
      : getOrOpenPort(parsed.path, parsed.baud)
  } catch (e) {
    return Promise.reject(new PokeTransportError(e.message, 'OPEN_ERROR'))
  }

  // Chain onto lock: only one command at a time per port
  const prev = entry.lock
  let unlockResolve
  entry.lock = new Promise(r => { unlockResolve = r })

  return prev.then(() => {
    return new Promise((resolve, reject) => {
      const frame = buildFrame(command, data)
      const timeout = timeoutMs || 10000

      const timer = setTimeout(() => {
        entry.onResp = null
        unlockResolve()
        reject(new PokeTransportError('serial timeout', 'TIMEOUT'))
      }, timeout)

      entry.onResp = {
        resolve: (val) => { unlockResolve(); resolve(val) },
        reject: (err) => { unlockResolve(); reject(err) },
        timer,
      }

      // Wait for port to be ready, then write
      const tryWrite = () => {
        if (entry.port.isOpen) {
          entry.port.write(frame, (err) => {
            if (err) {
              clearTimeout(timer)
              entry.onResp = null
              unlockResolve()
              reject(new PokeTransportError('write error: ' + err.message, 'WRITE_ERROR'))
            }
          })
        } else {
          setTimeout(tryWrite, 100)
        }
      }
      tryWrite()
    })
  })
}

// ── Public API ──

function pokeNodeSerial(endpoint, machineCode) {
  return serialCommand(endpoint, 'EXEC', machineCode)
}

function serialPing(endpoint) {
  return serialCommand(endpoint, 'PING', null, 3000)
}

function serialInfo(endpoint) {
  return serialCommand(endpoint, 'INFO', null, 5000)
}

function serialGpio(endpoint) {
  return serialCommand(endpoint, 'GPIO', null, 5000)
}

function serialTemp(endpoint) {
  return serialCommand(endpoint, 'TEMP', null, 5000)
}

function serialGpioSet(endpoint, pin, value) {
  return serialCommand(endpoint, 'GPOS', Buffer.from([pin, value ? 1 : 0]), 5000)
}

function serialEventMonitor(endpoint, config) {
  return serialCommand(endpoint, 'EMON', config, 5000)
}

function serialEventStop(endpoint) {
  return serialCommand(endpoint, 'ESTO', null, 5000)  // ESTOP = first 4 bytes "ESTO"
}

// ── List available serial ports ──
async function listSerialPorts() {
  try {
    const { SerialPort: SP } = require('serialport')
    const portList = await SP.list()
    return portList
      .filter(p => p.vendorId || p.manufacturer)
      .map(p => ({
        path: p.path,
        manufacturer: p.manufacturer || 'unknown',
        vendorId: p.vendorId,
        productId: p.productId,
        serialNumber: p.serialNumber,
      }))
  } catch (e) {
    return []
  }
}

// ── Close all ports (cleanup) ──
function closeAll() {
  for (const [, entry] of ports) {
    try {
      if (entry.port.destroy) entry.port.destroy()   // TCP socket
      else if (entry.port.isOpen) entry.port.close()  // SerialPort
    } catch {}
  }
  ports.clear()
  for (const [, entry] of udpSockets) {
    try { entry.socket.close() } catch {}
  }
  udpSockets.clear()
}

module.exports = {
  pokeNodeSerial,
  serialPing,
  serialInfo,
  serialGpio,
  serialTemp,
  serialGpioSet,
  serialEventMonitor,
  serialEventStop,
  serialCommand,
  listSerialPorts,
  parseSerialEndpoint,
  onSerialEvent,
  closeAll,
}
