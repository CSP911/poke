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
 */

const { log } = require('./logger')

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
  const match = endpoint.match(/^serial:\/\/(.+?)(\?.*)?$/)
  if (!match) return null
  const portPath = match[1]
  const params = new URLSearchParams(match[2] || '')
  const baud = parseInt(params.get('baud')) || 115200
  return { path: portPath, baud }
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

  // Frame parser: accumulate data and extract RESP frames
  let rxBuf = Buffer.alloc(0)
  port.on('data', (data) => {
    rxBuf = Buffer.concat([rxBuf, data])

    while (rxBuf.length >= 4) {
      // Find "RESP" magic — skip any ESP log output mixed in
      const respIdx = rxBuf.indexOf('RESP')
      if (respIdx < 0) {
        // Keep last 3 bytes (might be partial "RES")
        if (rxBuf.length > 3) rxBuf = rxBuf.slice(-3)
        break
      }
      if (respIdx > 0) rxBuf = rxBuf.slice(respIdx)
      if (rxBuf.length < 8) break

      const payloadLen = rxBuf.readUInt32LE(4)
      if (payloadLen > 1024 * 1024) {
        rxBuf = rxBuf.slice(4)
        continue
      }

      const frameSize = 8 + payloadLen
      if (rxBuf.length < frameSize) break  // wait for more data

      const payload = rxBuf.slice(8, frameSize).toString()
      rxBuf = rxBuf.slice(frameSize)

      // Resolve the in-flight request
      if (entry.onResp) {
        const cb = entry.onResp
        entry.onResp = null
        clearTimeout(cb.timer)
        cb.resolve(payload)
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
    return Promise.reject(new PokeTransportError('invalid serial endpoint: ' + endpoint, 'INVALID_ENDPOINT'))
  }

  let entry
  try {
    entry = getOrOpenPort(parsed.path, parsed.baud)
  } catch (e) {
    return Promise.reject(new PokeTransportError(e.message, 'SERIAL_OPEN_ERROR'))
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
              reject(new PokeTransportError('serial write error: ' + err.message, 'SERIAL_WRITE_ERROR'))
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
    if (entry.port.isOpen) entry.port.close()
  }
  ports.clear()
}

module.exports = {
  pokeNodeSerial,
  serialPing,
  serialInfo,
  serialGpio,
  serialTemp,
  serialCommand,
  listSerialPorts,
  parseSerialEndpoint,
  closeAll,
}
