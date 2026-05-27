/**
 * POKE 스트리밍 — raw HTTP (chunked 없이)
 */
const net = require('net')

const W = 40, H = 40
const FPS = 5
const TOTAL = 30

function makeFrame(n) {
  const px = Buffer.alloc(W*H*3)
  function set(x,y,r,g,b) {
    if(x<0||x>=W||y<0||y>=H)return
    const i=(y*W+x)*3; px[i]=r;px[i+1]=g;px[i+2]=b
  }
  function circ(cx,cy,rad,r,g,b) {
    for(let y=cy-rad;y<=cy+rad;y++)
      for(let x=cx-rad;x<=cx+rad;x++)
        if((x-cx)**2+(y-cy)**2<=rad*rad) set(x,y,r,g,b)
  }

  // bg
  for(let y=0;y<H;y++) for(let x=0;x<W;x++) set(x,y,10,10,25)

  // bouncing ball
  const t = n/TOTAL
  const bx = Math.round(10+(W-20)*(0.5+0.5*Math.sin(t*Math.PI*4)))
  const by = Math.round(10+(H-20)*Math.abs(Math.sin(t*Math.PI*6)))
  const r = Math.round(128+127*Math.sin(t*Math.PI*2))
  const g = Math.round(128+127*Math.sin(t*Math.PI*3))
  const b = Math.round(128+127*Math.sin(t*Math.PI*5))
  circ(bx,by,8,r,g,b)

  return px
}

function frmPacket(px) {
  const h = Buffer.alloc(11)
  h[0]=0x46;h[1]=0x52;h[2]=0x4D // FRM
  h.writeUInt16LE(W,3)
  h.writeUInt16LE(H,5)
  h.writeUInt32LE(px.length,7)
  return Buffer.concat([h, px])
}

// Calculate total body size
const str = Buffer.from('STR')
const end = Buffer.from('END')
let totalSize = str.length
for (let i = 0; i < TOTAL; i++) totalSize += 11 + W*H*3
totalSize += end.length

// Build HTTP request with Content-Length (no chunked)
const httpHeader = `POST /poke HTTP/1.1\r\nHost: localhost\r\nContent-Length: ${totalSize}\r\nContent-Type: application/octet-stream\r\n\r\n`

console.log(`Streaming ${TOTAL} frames, total ${totalSize} bytes`)

const sock = net.createConnection(8080, 'localhost', () => {
  // Send HTTP header
  sock.write(httpHeader)
  // Send STR
  sock.write(str)

  let frame = 0
  const iv = setInterval(() => {
    if (frame >= TOTAL) {
      sock.write(end)
      clearInterval(iv)
      console.log('Done')
      setTimeout(() => sock.end(), 1000)
      return
    }
    const px = makeFrame(frame)
    sock.write(frmPacket(px))
    frame++
    if (frame % 10 === 0) process.stdout.write(`Frame ${frame}/${TOTAL}\r`)
  }, 1000/FPS)
})

sock.on('data', d => console.log('Response:', d.toString()))
sock.on('error', e => console.error('Error:', e.message))
sock.on('close', () => { console.log('Closed'); process.exit() })
