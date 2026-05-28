/**
 * POKE 스트리밍 테스트 — 애니메이션 전송
 * node stream_test.js
 */
const net = require('net')
const http = require('http')

const W = 80, H = 80
const FPS = 10
const DURATION = 5 // seconds
const TOTAL_FRAMES = FPS * DURATION

function makeFrame(frameNum) {
  const pixels = Buffer.alloc(W * H * 3)

  function setPixel(x, y, r, g, b) {
    if (x < 0 || x >= W || y < 0 || y >= H) return
    const i = (y * W + x) * 3
    pixels[i] = r; pixels[i+1] = g; pixels[i+2] = b
  }

  function fillCircle(cx, cy, radius, r, g, b) {
    for (let y = cy-radius; y <= cy+radius; y++)
      for (let x = cx-radius; x <= cx+radius; x++)
        if ((x-cx)**2 + (y-cy)**2 <= radius**2) setPixel(x,y,r,g,b)
  }

  // Dark background
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++)
      setPixel(x, y, 10, 10, 25)

  // Bouncing ball
  const t = frameNum / TOTAL_FRAMES
  const bx = Math.round(10 + (W - 20) * (0.5 + 0.5 * Math.sin(t * Math.PI * 4)))
  const by = Math.round(10 + (H - 20) * Math.abs(Math.sin(t * Math.PI * 6)))
  const r = Math.round(128 + 127 * Math.sin(t * Math.PI * 2))
  const g = Math.round(128 + 127 * Math.sin(t * Math.PI * 3))
  const b = Math.round(128 + 127 * Math.sin(t * Math.PI * 5))
  fillCircle(bx, by, 8, r, g, b)

  // Trail
  for (let i = 1; i <= 5; i++) {
    const pt = (frameNum - i) / TOTAL_FRAMES
    if (pt < 0) continue
    const px = Math.round(10 + (W-20) * (0.5 + 0.5 * Math.sin(pt * Math.PI * 4)))
    const py = Math.round(10 + (H-20) * Math.abs(Math.sin(pt * Math.PI * 6)))
    const alpha = (5 - i) / 5
    fillCircle(px, py, 4, Math.round(r*alpha*0.3), Math.round(g*alpha*0.3), Math.round(b*alpha*0.3))
  }

  return pixels
}

function makeFramePacket(pixels) {
  // FRM + W(2) + H(2) + size(4) + RGB
  const size = pixels.length
  const header = Buffer.alloc(11)
  header[0] = 0x46; header[1] = 0x52; header[2] = 0x4D // FRM
  header.writeUInt16LE(W, 3)
  header.writeUInt16LE(H, 5)
  header.writeUInt32LE(size, 7)
  return Buffer.concat([header, pixels])
}

// HTTP POST with STR prefix, then stream frames
console.log(`Streaming ${TOTAL_FRAMES} frames at ${FPS}fps (${W}x${H})`)

const postBody = Buffer.from('STR')
const reqOptions = {
  hostname: 'localhost',
  port: 8080,
  path: '/poke',
  method: 'POST',
  headers: {
    'Content-Type': 'application/octet-stream',
    'Transfer-Encoding': 'chunked',
  },
  timeout: 30000,
}

const req = http.request(reqOptions, (res) => {
  let data = ''
  res.on('data', c => data += c)
  res.on('end', () => console.log('Response:', data))
})

req.on('error', e => console.error('Error:', e.message))

// Send STR header
req.write(postBody)

// Stream frames with delay
let frame = 0
const interval = setInterval(() => {
  if (frame >= TOTAL_FRAMES) {
    // Send END marker
    req.write(Buffer.from('END'))
    req.end()
    clearInterval(interval)
    console.log('Stream complete')
    return
  }

  const pixels = makeFrame(frame)
  const packet = makeFramePacket(pixels)
  req.write(packet)
  frame++

  if (frame % 10 === 0) process.stdout.write(`Frame ${frame}/${TOTAL_FRAMES}\r`)
}, 1000 / FPS)
