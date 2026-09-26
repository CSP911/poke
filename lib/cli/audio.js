/* Voice I/O — energy-VAD mic recording, local whisper STT, TTS, wake word */
const fs = require('fs')
const path = require('path')
const { spawn, execFileSync } = require('child_process')

const { ROOT } = require('./llm')
const WHISPER_MODEL = path.join(ROOT, 'data/models/ggml-small.bin')

/* Record with voice-activity detection: streams raw PCM from the mic,
 * watches per-100ms RMS levels, and stops after 1.2s of trailing silence
 * (the same energy-VAD + silence-timer endpointing commercial assistants
 * use as their base layer). Enter still works as a manual stop.
 * Resolves true if speech was detected. */
/* how long to wait for speech to start (100ms frames); POKE_VAD_WAIT_MS overrides (default 8s) */
const WAIT_FRAMES = Math.max(10, Math.round((parseInt(process.env.POKE_VAD_WAIT_MS) || 8000) / 100))

function recordWithVAD(wavPath, opts = {}) {
  return new Promise((resolve, reject) => {
    const SR = 16000
    const FRAME = SR * 2 / 10               /* 100ms of s16le mono = 3200B */
    const rec = spawn('ffmpeg', [
      '-y', '-f', 'avfoundation', '-i', ':0',
      '-ar', String(SR), '-ac', '1', '-f', 's16le', '-',
    ], { stdio: ['ignore', 'pipe', 'pipe'] })

    const chunks = []
    let pending = Buffer.alloc(0)
    let frames = 0, spoken = false, silentRun = 0
    let noise = 0, noiseFrames = 0
    let done = false

    const finish = (err) => {
      if (done) return
      done = true
      if (process.stdin.isTTY) { process.stdin.setRawMode(false); process.stdin.pause() }
      rec.kill('SIGKILL')
      process.stdout.write('\n')
      if (err) return reject(err)
      /* wrap raw PCM in a 44-byte WAV header */
      const pcm = Buffer.concat(chunks)
      const h = Buffer.alloc(44)
      h.write('RIFF', 0); h.writeUInt32LE(36 + pcm.length, 4); h.write('WAVE', 8)
      h.write('fmt ', 12); h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20)
      h.writeUInt16LE(1, 22); h.writeUInt32LE(SR, 24); h.writeUInt32LE(SR * 2, 28)
      h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34)
      h.write('data', 36); h.writeUInt32LE(pcm.length, 40)
      fs.writeFileSync(wavPath, Buffer.concat([h, pcm]))
      resolve(spoken)
    }

    rec.on('error', (e) => finish(e))
    if (!opts.quiet) console.log('\n🎤 listening... (stops by itself when you finish speaking)')

    if (process.stdin.isTTY && !opts.daemon) {
      process.stdin.setRawMode(true)
      process.stdin.resume()
      process.stdin.once('data', () => finish())
    }

    rec.stdout.on('data', (d) => {
      chunks.push(d)
      pending = Buffer.concat([pending, d])
      while (pending.length >= FRAME) {
        const frame = pending.subarray(0, FRAME)
        pending = pending.subarray(FRAME)
        frames++

        let sum = 0
        for (let i = 0; i < FRAME; i += 2) { const s = frame.readInt16LE(i); sum += s * s }
        const rms = Math.sqrt(sum / (FRAME / 2))

        /* first 0.5s calibrates the noise floor */
        if (noiseFrames < 5) { noise = (noise * noiseFrames + rms) / ++noiseFrames; continue }
        const speechThresh = Math.max(noise * 3.5, 350)
        const silenceThresh = Math.max(noise * 2.0, 250)

        const level = Math.min(30, Math.round(rms / 100))
        process.stdout.write('\r  ' + '█'.repeat(level).padEnd(30) + (spoken ? ' ●' : '  '))

        if (rms > speechThresh) { spoken = true; silentRun = 0 }
        else if (spoken && rms < silenceThresh && ++silentRun >= 12) return finish()  /* 1.2s */

        if (!spoken && frames > WAIT_FRAMES)
          return finish(opts.daemon ? null : new Error(`heard no speech for ${WAIT_FRAMES / 10}s`))
        if (frames > 200) return finish()   /* hard cap: 20s */
      }
    })
  })
}

/* Assistant language: 'en' (default) or 'ko' — set POKE_LANG=ko for the
 * Korean voice assistant (wake word "헥스야", Korean STT and replies). */
const LANG = process.env.POKE_LANG === 'ko' ? 'ko' : 'en'

function transcribe(wavPath) {
  if (!fs.existsSync(WHISPER_MODEL)) {
    throw new Error('whisper model missing — see data/models/ (ggml-small.bin)')
  }
  const out = execFileSync('whisper-cli', [
    '-m', WHISPER_MODEL, '-l', LANG, '-f', wavPath,
    '--no-timestamps', '--no-prints',
  ], { encoding: 'utf8' })
  return out.trim().replace(/\s+/g, ' ')
}

function speak(t) { try { execFileSync('say', [t]) } catch {} }

/* ── wake word ── */
const WAKES_KO = ['헥스', '핵스', '헥사', '헥쓰', '헤스', 'hex']

function stripWake(text) {
  if (LANG === 'ko') {
    const norm = text.replace(/[\s.,!?~…'"「」]/g, '')
    const lower = norm.toLowerCase()
    for (const w of WAKES_KO) {
      const i = lower.indexOf(w)
      if (i >= 0 && i <= 6) {   /* wake word near the start */
        const rest = norm.slice(i + w.length).replace(/^(야|아|씨|님|,)/, '')
        return { woke: true, rest }
      }
    }
    return { woke: false, rest: '' }
  }
  /* en: "hex, ..." or "hey hex ..." at the start; keep spaces intact */
  const m = text.trim().match(/^\W*(?:ok(?:ay)?\s+|hey\s+)?(?:hex|hacks)\b[\s,.!?-]*(.*)$/i)
  if (m) return { woke: true, rest: m[1].trim() }
  return { woke: false, rest: '' }
}

module.exports = { LANG, recordWithVAD, transcribe, speak, stripWake }
