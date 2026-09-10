/* Daemons — autonomous device events (EVNT :5556) and the always-on
 * wake-word assistant ("hex, ..." — or "헥스야" with POKE_LANG=ko). */
const dgram = require('dgram')
const fs = require('fs')
const path = require('path')
const os = require('os')
const { execFileSync } = require('child_process')

const { ROOT, makeClient, textOf } = require('./llm')
const { become } = require('./persona')
const { LANG, recordWithVAD, transcribe, speak, stripWake } = require('./audio')

const EVNT_PORT = 5556

/* Personas fire EVNT packets when a condition the user asked about
 * happens. The LLM turns the raw event into a human notification. */
function startEvents(client) {
  const sock = dgram.createSocket('udp4')

  sock.on('message', async (m, rinfo) => {
    if (m.length < 16 || m.toString('utf8', 0, 4) !== 'EVNT') return
    const code = m.readUInt32LE(4)
    const value = Number(m.readBigUInt64LE(8))
    let lastWish = ''
    try { lastWish = fs.readFileSync(path.join(ROOT, 'data/last-wish.txt'), 'utf8').trim() } catch {}
    console.log(`\n⚡ event from ${rinfo.address}: code=${code} value=${value}`)

    try {
      const msg = await client.messages.create({
        model: process.env.POKE_MATCH_MODEL || 'claude-sonnet-5',
        max_tokens: 200,
        system: `A bare-metal edge device running a user-requested persona fired an
autonomous event. Interpret it for the user. Respond JSON only:
{"speak":"<one short ${LANG === 'ko' ? 'Korean' : 'English'} sentence to say aloud>","notify":"<short notification text>"}
Context: the persona was created from this user request: "${lastWish}".
Event value conventions: temperatures are in milli-celsius (47500 = 47.5°C),
times in seconds, others are raw numbers.`,
        messages: [{ role: 'user', content: `event code=${code} value=${value}` }],
      })
      const j = JSON.parse(textOf(msg))
      console.log(`🔔 ${j.notify}`)
      execFileSync('osascript', ['-e',
        `display notification ${JSON.stringify(j.notify)} with title "POKE" sound name "Glass"`])
      speak(j.speak)
    } catch (e) {
      console.error('event handling failed:', e.message)
    }
  })

  sock.bind(EVNT_PORT, () => {
    console.log(`👂 listening for device events on :${EVNT_PORT}`)
  })
  return sock
}

async function listen() {
  startEvents(makeClient())
  await new Promise(() => {})  /* run forever */
}

/* One utterance: record → transcribe → become */
async function voice(request) {
  const wav = path.join(os.tmpdir(), 'poke-voice.wav')
  await recordWithVAD(wav)
  console.log('… transcribing (local whisper)')
  const wish = transcribe(wav)
  fs.rmSync(wav, { force: true })
  if (!wish) throw new Error('heard nothing — try again closer to the mic')
  console.log(`\n🗣  "${wish}"`)
  await become(request, wish)
}

/* Always-on assistant: wake word + event listener in one daemon */
async function hexDaemon(request) {
  startEvents(makeClient())
  console.log(`🤖 HEX standing by — say "${LANG === 'ko' ? '헥스야, ...' : 'hex, ...'}" (Ctrl+C to stop)`)

  const wav = path.join(os.tmpdir(), 'poke-hex.wav')
  let awaitUntil = 0

  while (true) {
    let spoke = false
    try { spoke = await recordWithVAD(wav, { daemon: true, quiet: true }) } catch { continue }
    if (!spoke) continue

    let text = ''
    try { text = transcribe(wav) } catch { continue }
    if (!text || text.length < 2) continue
    console.log(`\n🗣  "${text}"`)

    const { woke, rest } = stripWake(text)
    let cmdText = ''
    if (woke) {
      if (rest.length >= 2) cmdText = rest
      else { speak(LANG === 'ko' ? '네?' : 'Yes?'); awaitUntil = Date.now() + 15000; continue }
    } else if (Date.now() < awaitUntil) {
      cmdText = text
    } else continue

    awaitUntil = 0
    speak(LANG === 'ko' ? '네' : 'Okay')
    try {
      await become(request, cmdText)
      speak(LANG === 'ko' ? '완료했습니다' : 'Done')
    } catch (e) {
      console.error('become failed:', e.message)
      speak(LANG === 'ko' ? '실패했어요' : 'That failed')
    }
  }
}

module.exports = { listen, voice, hexDaemon }
