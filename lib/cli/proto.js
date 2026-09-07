/* POKE UDP protocol — framing, request, payload builders (see PROTOCOL.md §10) */
const dgram = require('dgram')

const PORT = 5555
const TZ_OFFSET_MS = 9 * 3600 * 1000  /* KST */

function frame(payload) {
  const pkt = Buffer.alloc(8 + payload.length)
  pkt.write('POKE', 0)
  pkt.writeUInt32LE(payload.length, 4)
  payload.copy(pkt, 8)
  return pkt
}

/* Returns a request(payload, timeoutMs) bound to one edge host. */
function connect(host) {
  return function request(payload, timeoutMs = 3000) {
    return new Promise((resolve, reject) => {
      const sock = dgram.createSocket('udp4')
      const timer = setTimeout(() => { sock.close(); reject(new Error(`no response from ${host}`)) }, timeoutMs)
      sock.once('message', (m) => {
        clearTimeout(timer)
        sock.close()
        resolve(m.slice(8, 8 + m.readUInt32LE(4)).toString())
      })
      sock.send(frame(payload), PORT, host)
    })
  }
}

function timePayload() {
  const b = Buffer.alloc(12)
  b.write('TIME', 0)
  b.writeBigUInt64LE(BigInt(Date.now() + TZ_OFFSET_MS), 4)
  return b
}

function pparPayload(values) {
  const b = Buffer.alloc(5 + values.length * 8)
  b.write('PPAR', 0)
  b[4] = values.length
  values.forEach((v, i) => b.writeBigUInt64LE(BigInt(Math.max(0, Math.round(v))), 5 + i * 8))
  return b
}

/* DRAW op builders */
const opClear = (color) => { const b = Buffer.alloc(5); b[0] = 1; b.writeUInt32LE(color, 1); return b }
const opRect = (x, y, w, h, color) => {
  const b = Buffer.alloc(13); b[0] = 2
  b.writeInt16LE(x, 1); b.writeInt16LE(y, 3); b.writeInt16LE(w, 5); b.writeInt16LE(h, 7)
  b.writeUInt32LE(color, 9); return b
}
const opText = (x, y, scale, color, s) => {
  const t = Buffer.from(s)
  const b = Buffer.alloc(11 + t.length); b[0] = 3
  b.writeInt16LE(x, 1); b.writeInt16LE(y, 3); b[5] = scale
  b.writeUInt32LE(color, 6); b[10] = t.length; t.copy(b, 11); return b
}

module.exports = { PORT, connect, timePayload, pparPayload, opClear, opRect, opText }
