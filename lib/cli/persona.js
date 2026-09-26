/* Persona pipeline — cache catalog, LLM codegen (become), hot parameters.
 *
 * become(): natural language → cached binary + PPAR values (~1s), or
 * fresh LLM-written C → aarch64 flat binary → PRUN (then auto-cached). */
const fs = require('fs')
const path = require('path')
const { execFileSync } = require('child_process')

const { ROOT, makeClient, textOf } = require('./llm')
const { timePayload, pparPayload, upload, PERSONA_MAX } = require('./proto')

const PI4_DIR = path.join(ROOT, 'edge/kernel/pi4')
const PERSONA_DIR = path.join(PI4_DIR, 'personas')
const CACHE_DIR = path.join(ROOT, 'data/persona-cache')
const CUR_FILE = path.join(ROOT, 'data/current-persona.txt')

/* ── catalog ── */
function loadCatalog() {
  try { return JSON.parse(fs.readFileSync(path.join(CACHE_DIR, 'catalog.json'), 'utf8')) }
  catch { return [] }
}

function saveCatalog(cat) {
  fs.mkdirSync(CACHE_DIR, { recursive: true })
  fs.writeFileSync(path.join(CACHE_DIR, 'catalog.json'), JSON.stringify(cat, null, 2))
}

function cacheStore(name, description, params, code, bin) {
  fs.mkdirSync(CACHE_DIR, { recursive: true })
  const safe = name.toLowerCase().replace(/[^a-z0-9-]/g, '-').slice(0, 40) || 'persona'
  fs.writeFileSync(path.join(CACHE_DIR, `${safe}.c`), code)
  fs.writeFileSync(path.join(CACHE_DIR, `${safe}.bin`), bin)
  const cat = loadCatalog().filter((e) => e.name !== safe)
  cat.push({ name: safe, description, params, created: new Date().toISOString() })
  saveCatalog(cat)
  return safe
}

/* hub-side note of which persona the device is running (for hot params) */
function getCurrent() { try { return fs.readFileSync(CUR_FILE, 'utf8').trim() } catch { return '' } }
function setCurrent(name) { try { fs.writeFileSync(CUR_FILE, name) } catch {} }
function clearCurrent() { try { fs.rmSync(CUR_FILE, { force: true }) } catch {} }

/* ── request → cached persona + parameter values (fast path router) ── */
async function matchCache(client, wish, catalog) {
  const listing = catalog.map((e) =>
    `- ${e.name}: ${e.description}\n  params: ${e.params.map((p, i) => `[${i}] ${p.name} — ${p.desc}`).join('; ') || '(none)'}`
  ).join('\n')
  const msg = await client.messages.create({
    model: process.env.POKE_MATCH_MODEL || 'claude-sonnet-5',
    max_tokens: 300,
    system: `You route requests for a transformable display device to a catalog of cached
"personas". Respond with JSON ONLY, no prose.

Catalog:
${listing}

If one cached persona can FULLY serve the request by choosing parameter values,
respond: {"match":"<name>","values":[<numbers in declared param order>]}
Convert units: durations to seconds, temperatures to celsius. Use 0 to keep a
parameter's default. If the request needs behavior or a look that no cached
persona provides, respond: {"match":null}. When unsure, prefer null.`,
    messages: [{ role: 'user', content: wish }],
  })
  try { return JSON.parse(textOf(msg)) }
  catch { return { match: null } }
}

function personaSystemPrompt() {
  const api = fs.readFileSync(path.join(PI4_DIR, 'poke_api.h'), 'utf8')
  const example = fs.readFileSync(path.join(PERSONA_DIR, 'clock.c'), 'utf8')
  return `You are HEX, the applet generator of POKE — an open-source hobbyist platform
for the user's own Raspberry Pi 4. POKE's firmware runs display applets called
"personas": tiny freestanding C programs (no OS, no libc) that the firmware loads
and calls every 50ms with an API table. A persona turns the device's little LCD
into a clock, a timer, a thermometer — whatever the user asks for.

The API (poke_api.h):
${api}

HARD CONSTRAINTS — violating any of these crashes the device or fails the build:
- Output ONLY the C source file, nothing else. No markdown fences, no explanation.
- Exactly one entry point:
    __attribute__((section(".text.main")))
    unsigned long persona_main(const api_t *api, unsigned long tick)
- #include "../poke_api.h" and NOTHING else. No libc calls (no printf/memset/etc).
- NO global or static VARIABLES (the binary has no .data/.bss). static helper
  FUNCTIONS are fine. All state must be derived from tick, api->ms(), api->clock().
- Integer arithmetic only. No float, no double.
- Compiled flat binary must stay under 8 KB (8192 bytes): keep it lean, no big
  arrays or lookup tables.
- Display: 1024x600, 32-bit color 0x00RRGGBB. api->text renders 8px-per-char glyphs
  scaled by 'scale' (char width = 8*scale px). Screen top-left is (0,0).
- Draw the static parts (title, background) only once at tick==0. For live values,
  pace redraws with "if (tick % N) return 0;" and clear just the changed region with
  api->rect(background color) before re-drawing text — this avoids flicker.
- api->clock() returns LOCAL wall-clock time as epoch seconds (0 if not set yet).
  api->ms() is milliseconds since boot. tick increments every 50ms from injection.
- api->temp_mc() returns SoC temperature in milli-celsius (e.g. 47500 = 47.5C).
- api->gpio_out(pin, val) drives a GPIO pin; valid pins 2-27 except 14,15.
- api->emit(code, value) fires an autonomous event to the hub — use it when the
  user asks to be NOTIFIED/ALERTED about a condition ("tell me when...", "warn
  me if..."). Pick a small integer code per condition; the kernel rate-limits to
  one event per 10s per code, so it is safe to call every tick while the
  condition holds. Values: temperatures in milli-celsius, times in seconds.
- api->touch(&x, &y) fills the touch position in screen coords and returns:
  0 = not pressed, 1 = held down, 2 = NEW TAP (returned exactly once per press).
  Use ==2 for buttons/taps, >=1 for dragging. Check it every tick (do not gate
  touch handling behind "if (tick % N) return").
- PARAMETERIZE: any user-tunable constant (a duration, a threshold, a target
  value) must NOT be hard-coded. Read it with api->param(i) (returns 0 when
  unset — fall back to a sensible default). This lets the same binary be reused
  with different values later.
- The VERY FIRST line of the file must be a metadata comment on one line:
  // POKE-META {"name":"<kebab-name>","description":"<one line, generic — describe the persona family, not this request>","params":[{"name":"<param>","desc":"<meaning, unit, default>"}],"values":[<the values THIS request needs, in order>]}

A reference persona in the exact expected style:
${example}

Design a clean, legible screen for what the user asks. Title at top, big live value
in the middle. Respond with the complete C file only.`
}

async function become(request, wish) {
  const client = makeClient()
  const t0 = Date.now()
  try { fs.writeFileSync(path.join(ROOT, 'data/last-wish.txt'), wish) } catch {}

  /* ── fast path: cached persona + runtime params ── */
  const catalog = loadCatalog()
  if (catalog.length) {
    const m = await matchCache(client, wish, catalog)
    if (m && m.match) {
      const entry = catalog.find((e) => e.name === m.match)
      const binPath = path.join(CACHE_DIR, `${m.match}.bin`)
      if (entry && fs.existsSync(binPath)) {
        const values = (m.values || []).map(Number)
        console.log(`\n⚡ cache hit: ${m.match}${values.length ? ` (${entry.params.map((p, i) => `${p.name}=${values[i]}`).join(', ')})` : ''}`)

        /* hot parameters: same persona already running → adjust in place,
         * no reinjection, running state (e.g. elapsed time) survives */
        if (values.length && getCurrent() === m.match) {
          let running = false
          try { running = JSON.parse(await request(Buffer.from('INFO'))).persona === true } catch {}
          if (running) {
            await request(pparPayload(values))
            console.log(`✦ hot-adjusted in ${((Date.now() - t0) / 1000).toFixed(1)}s — no reinjection, state kept`)
            return
          }
        }

        const bin = fs.readFileSync(binPath)
        await request(pparPayload(values.length ? values : [0]))
        await request(timePayload())
        const r = JSON.parse(await upload(request, 'PRUN', bin))
        setCurrent(m.match)
        console.log(`✦ device transformed in ${((Date.now() - t0) / 1000).toFixed(1)}s — ${r.size} bytes replayed from cache`)
        return
      }
    }
    console.log('(no cached persona fits — generating fresh)')
  }

  /* ── slow path: LLM writes a fresh persona ── */
  const srcPath = path.join(PERSONA_DIR, 'llm.c')
  const messages = [{ role: 'user', content: wish }]

  for (let attempt = 1; attempt <= 3; attempt++) {
    console.log(`\n[1/4] HEX thinking... (attempt ${attempt})`)
    const msg = await client.messages.create({
      /* sonnet-5: best hit-rate for persona codegen (fable-5/opus-5 refuse
       * or truncate this task via API — override with POKE_MODEL to retest) */
      model: process.env.POKE_MODEL || 'claude-sonnet-5',
      max_tokens: 8000,
      system: personaSystemPrompt(),
      messages,
    })
    const code = textOf(msg)
    if (!code) {
      console.error(`no text in response (stop_reason=${msg.stop_reason})`)
      continue
    }

    console.log('\n──── generated persona ─────────────────────────')
    console.log(code)
    console.log('────────────────────────────────────────────────')

    fs.writeFileSync(srcPath, code + '\n')
    console.log('\n[2/4] compiling to aarch64 machine code...')
    try {
      fs.rmSync(path.join(PERSONA_DIR, 'llm.bin'), { force: true })
      execFileSync('make', ['-C', PI4_DIR, 'personas/llm.bin'], { stdio: ['ignore', 'pipe', 'pipe'] })
    } catch (e) {
      const err = (e.stderr || '').toString().slice(0, 2000)
      console.error('compile failed:\n' + err)
      messages.push({ role: 'assistant', content: code })
      messages.push({ role: 'user', content: `Compilation failed:\n${err}\nFix it. Respond with the complete corrected C file only.` })
      continue
    }

    const bin = fs.readFileSync(path.join(PERSONA_DIR, 'llm.bin'))
    console.log(`[3/4] binary ready: ${bin.length} bytes`)
    if (bin.length > PERSONA_MAX) {
      messages.push({ role: 'assistant', content: code })
      messages.push({ role: 'user', content: `Binary is ${bin.length} bytes — over the ${PERSONA_MAX} byte limit. Make it smaller. Respond with the complete C file only.` })
      continue
    }

    /* parse POKE-META header (for caching + initial params) */
    let meta = null
    const mm = code.split('\n')[0].match(/POKE-META\s+(\{.*\})/)
    if (mm) { try { meta = JSON.parse(mm[1]) } catch {} }

    console.log('[4/4] loading onto bare metal...')
    const values = (meta && meta.values || []).map(Number)
    await request(pparPayload(values.length ? values : [0]))
    await request(timePayload())
    const r = JSON.parse(await upload(request, 'PRUN', bin))
    console.log(`\n✦ device transformed in ${((Date.now() - t0) / 1000).toFixed(1)}s (${r.size} bytes of volatile machine code)`)

    if (meta && meta.name) {
      const saved = cacheStore(meta.name, meta.description || wish, meta.params || [], code, bin)
      setCurrent(saved)
      console.log(`  cached as "${saved}" — next time this family is ~2s`)
    } else setCurrent('llm')
    return
  }
  throw new Error('failed after 3 attempts')
}

module.exports = {
  PERSONA_DIR, CACHE_DIR,
  loadCatalog, become,
  getCurrent, setCurrent, clearCurrent,
}
