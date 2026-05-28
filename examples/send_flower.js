/**
 * 꽃 이미지를 생성해서 POKE OS에 전송
 * node send_flower.js
 */
const http = require('http')

const W = 100
const H = 100
const pixels = Buffer.alloc(W * H * 3)

function setPixel(x, y, r, g, b) {
  if (x < 0 || x >= W || y < 0 || y >= H) return
  const i = (y * W + x) * 3
  pixels[i] = r
  pixels[i + 1] = g
  pixels[i + 2] = b
}

function fillCircle(cx, cy, radius, r, g, b) {
  for (let y = cy - radius; y <= cy + radius; y++) {
    for (let x = cx - radius; x <= cx + radius; x++) {
      const dx = x - cx, dy = y - cy
      if (dx * dx + dy * dy <= radius * radius)
        setPixel(x, y, r, g, b)
    }
  }
}

function fillEllipse(cx, cy, rx, ry, r, g, b) {
  for (let y = cy - ry; y <= cy + ry; y++) {
    for (let x = cx - rx; x <= cx + rx; x++) {
      const dx = x - cx, dy = y - cy
      if ((dx*dx)/(rx*rx) + (dy*dy)/(ry*ry) <= 1)
        setPixel(x, y, r, g, b)
    }
  }
}

// Background - dark green
for (let y = 0; y < H; y++)
  for (let x = 0; x < W; x++)
    setPixel(x, y, 15, 30, 15)

// Stem
for (let y = 55; y < 90; y++)
  for (let x = 48; x <= 52; x++)
    setPixel(x, y, 30, 140, 30)

// Leaves
fillEllipse(40, 72, 12, 5, 40, 160, 40)
fillEllipse(60, 68, 12, 5, 40, 160, 40)

// Petals (5 petals around center)
const petalColors = [
  [255, 100, 120],  // pink
  [255, 130, 100],  // salmon
  [255, 80, 140],   // hot pink
  [255, 120, 130],  // light pink
  [255, 90, 110],   // rose
]
const cx = 50, cy = 40, petalR = 14
for (let i = 0; i < 5; i++) {
  const angle = (i / 5) * Math.PI * 2 - Math.PI / 2
  const px = cx + Math.cos(angle) * 13
  const py = cy + Math.sin(angle) * 13
  const [r, g, b] = petalColors[i]
  fillCircle(Math.round(px), Math.round(py), petalR, r, g, b)
}

// Center
fillCircle(cx, cy, 8, 255, 220, 50)
fillCircle(cx, cy, 5, 255, 200, 30)

// Build POKE image packet: "IMG" + width(2 LE) + height(2 LE) + RGB
const header = Buffer.alloc(7)
header[0] = 0x49 // I
header[1] = 0x4D // M
header[2] = 0x47 // G
header.writeUInt16LE(W, 3)
header.writeUInt16LE(H, 5)

const body = Buffer.concat([header, pixels])

console.log(`Sending flower: ${W}x${H} = ${body.length} bytes`)

const req = http.request({
  hostname: 'localhost',
  port: 8080,
  path: '/poke',
  method: 'POST',
  headers: {
    'Content-Type': 'application/octet-stream',
    'Content-Length': body.length,
  },
  timeout: 10000,
}, (res) => {
  let data = ''
  res.on('data', chunk => data += chunk)
  res.on('end', () => console.log('Response:', data))
})

req.on('error', e => console.error('Error:', e.message))
req.on('timeout', () => { req.destroy(); console.log('Timeout') })
req.write(body)
req.end()
