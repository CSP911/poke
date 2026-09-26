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

/* Transport limits. One UDP frame carries ~1460 bytes of payload before the
 * sender's IP layer fragments it — and the bare-metal edge does not
 * reassemble fragments — so anything bigger goes up in EXLD chunks. */
const SINGLE_MAX = 1400      /* largest binary sent as one EXEC/PRUN frame */
const CHUNK = 1024           /* EXLD chunk size */
const STAGE_MAX = 8192       /* edge stage_buf — the real persona size limit */
const PERSONA_MAX = STAGE_MAX

function fletcher32(buf) {
  let s1 = 0, s2 = 0
  for (const b of buf) { s1 = (s1 + b) % 65535; s2 = (s2 + s1) % 65535 }
  return ((s2 << 16) | s1) >>> 0
}

/* Upload a binary and run it. kind = 'EXEC' (probe, reply is its output) or
 * 'PRUN' (persona, reply is JSON). Small binaries go as one frame; larger ones
 * are staged with EXLD chunks (each acked) and finished with EXRN / PRST,
 * which carry total length + Fletcher-32 so a lost chunk is refused. */
async function upload(request, kind, bin, timeoutMs = 15000) {
  if (bin.length <= SINGLE_MAX) return request(Buffer.concat([Buffer.from(kind), bin]), timeoutMs)
  if (bin.length > STAGE_MAX) throw new Error(`binary is ${bin.length} bytes, edge stage limit is ${STAGE_MAX}`)
  const u32 = (v) => { const b = Buffer.alloc(4); b.writeUInt32LE(v >>> 0, 0); return b }
  for (let off = 0; off < bin.length; off += CHUNK) {
    const part = bin.slice(off, off + CHUNK)
    let ack = ''
    for (let t = 0; t < 3 && !ack.startsWith('ok'); t++) {
      try { ack = await request(Buffer.concat([Buffer.from('EXLD'), u32(off), part]), 2000) } catch (e) { ack = e.message }
    }
    if (!ack.startsWith('ok')) throw new Error(`chunk upload failed at ${off}: ${ack}`)
  }
  const fin = kind === 'PRUN' ? 'PRST' : 'EXRN'
  return request(Buffer.concat([Buffer.from(fin), u32(bin.length), u32(fletcher32(bin))]), timeoutMs)
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

module.exports = { PORT, SINGLE_MAX, PERSONA_MAX, connect, upload, fletcher32, timePayload, pparPayload, opClear, opRect, opText }
