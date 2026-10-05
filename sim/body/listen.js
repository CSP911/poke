#!/usr/bin/env node
/* Continuous commentary listener for voice coaching.
 *
 * Unlike `poke voice` (one utterance, then stop), this keeps one microphone
 * stream open and cuts it into utterances with an energy VAD, so a person
 * can talk while they watch: "움직였어 … 검지 말았어 … 오히려 풀렸어 … 쥐었어".
 * Every utterance is transcribed with local whisper and printed as one JSON
 * line with the wall-clock time speech started and ended (ms), so the coach
 * can line it up with what the body was doing a moment before.
 *
 *   node listen.js            → {"t0":…, "t1":…, "text":"…"} per line
 */
const fs = require('fs')
const os = require('os')
const path = require('path')
const { spawn } = require('child_process')
process.env.POKE_LANG = process.env.POKE_LANG || 'ko'
const { transcribe } = require('../../lib/cli/audio')

const SR = 16000, FRAME = SR * 2 / 10          /* 100 ms of s16le mono */
const TAIL = 6                                 /* 0.6 s of silence ends an utterance */
const PRE = 3                                  /* keep 0.3 s before onset */

const rec = spawn('ffmpeg', ['-loglevel', 'quiet', '-f', 'avfoundation', '-i', ':0',
  '-ar', String(SR), '-ac', '1', '-f', 's16le', '-'], { stdio: ['ignore', 'pipe', 'ignore'] })

let pending = Buffer.alloc(0), noise = 0, nf = 0
let ring = [], cur = null, silent = 0, seq = 0
const queue = []; let busy = false

function emit(obj) { process.stdout.write(JSON.stringify(obj) + '\n') }

function work() {
  if (busy || !queue.length) return
  busy = true
  const u = queue.shift()
  setImmediate(() => {
    try {
      const text = transcribe(u.wav)
      if (text && !/^\[.*\]$/.test(text)) emit({ t0: u.t0, t1: u.t1, text })
    } catch (e) { emit({ error: e.message }) }
    try { fs.unlinkSync(u.wav) } catch {}
    busy = false; work()
  })
}

function close(now) {
  const pcm = Buffer.concat(cur.frames)
  if (cur.speechFrames >= 3) {                 /* ignore clicks shorter than 0.3 s */
    const wav = path.join(os.tmpdir(), `poke-coach-${process.pid}-${seq++}.wav`)
    const h = Buffer.alloc(44)
    h.write('RIFF', 0); h.writeUInt32LE(36 + pcm.length, 4); h.write('WAVE', 8)
    h.write('fmt ', 12); h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22)
    h.writeUInt32LE(SR, 24); h.writeUInt32LE(SR * 2, 28); h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34)
    h.write('data', 36); h.writeUInt32LE(pcm.length, 40)
    fs.writeFileSync(wav, Buffer.concat([h, pcm]))
    queue.push({ wav, t0: cur.t0, t1: now - TAIL * 100 }); work()
  }
  cur = null; silent = 0
}

rec.stdout.on('data', (d) => {
  pending = Buffer.concat([pending, d])
  while (pending.length >= FRAME) {
    const f = pending.subarray(0, FRAME); pending = pending.subarray(FRAME)
    let sum = 0
    for (let i = 0; i < FRAME; i += 2) { const s = f.readInt16LE(i); sum += s * s }
    const rms = Math.sqrt(sum / (FRAME / 2)), now = Date.now()
    if (nf < 10) { noise = (noise * nf + rms) / ++nf; continue }  /* 1 s noise floor */
    const speech = rms > Math.max(noise * 3.0, 300)
    if (!speech && !cur) noise = noise * 0.995 + rms * 0.005        /* track slow drift */
    if (cur) {
      cur.frames.push(Buffer.from(f))
      if (speech) { silent = 0; cur.speechFrames++ } else if (++silent >= TAIL) close(now)
      if (cur && cur.frames.length > 150) close(now)                /* 15 s cap */
    } else if (speech) {
      cur = { t0: now - PRE * 100, frames: [...ring, Buffer.from(f)], speechFrames: 1 }
    }
    ring.push(Buffer.from(f)); if (ring.length > PRE) ring.shift()
  }
})
emit({ ready: true })
process.on('SIGTERM', () => { rec.kill('SIGKILL'); process.exit(0) })
