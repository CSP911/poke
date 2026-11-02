#!/usr/bin/env node
/* Skill generation: a body (learned by discovery) + an intent → a C skill.
 *   node generate.js <body.json> "<intent>" <out.c> [feedback]
 * Prints the SKILL-META JSON (name, params with search ranges) on stdout.
 * The language model writes the structure of the movement; the numbers it
 * exposes as params are tuned afterwards on the body itself (tune.py). */
const fs = require('fs')
const path = require('path')
const { makeClient, textOf } = require('../../lib/cli/llm')

const [bodyPath, intent, outPath, feedback] = process.argv.slice(2)
if (!bodyPath || !intent || !outPath) { console.error('usage: generate.js <body.json> "<intent>" <out.c> [feedback]'); process.exit(1) }
const body = JSON.parse(fs.readFileSync(bodyPath, 'utf8'))
const abi = fs.readFileSync(path.join(__dirname, '..', '..', 'edge', 'kernel', 'pi4', 'motion_io.h'), 'utf8')

const system = `You write motion skills for a modular robot body, in C.

A skill is a volatile program: it is generated for one body and one intent,
runs, and is discarded. It speaks this ABI (motion_io.h):

${abi}

Rules:
- Output ONE complete C file and nothing else. First line must be:
  // SKILL-META {"name":"<kebab-name>","idea":"<one sentence: how the body moves>","params":[{"name":"<p0>","lo":<min>,"hi":<max>}, ...]}
- #include "motion_io.h" and <math.h> only (sinf, cosf, fabsf, etc. are fine).
- Define void skill_step(motion_io_t *io). It is called ${100} times per second.
- Expose every number you are unsure of as io->param[i] (at most 8), in the
  order listed in SKILL-META, with a sensible search range. A tuner will search
  those ranges on the real body; your job is the STRUCTURE of the movement.
  If a param is 0 (unset), use the middle of its range.
- Write targets to io->cmd[j] for j < io->njoints. Angles in radians, within
  ±io->limit[j]. A safety layer clamps angles and limits speed to ~6 rad/s, so
  smooth periodic motion works better than jumps.
- Use io->axis[j] (0 = bends up/down, 1 = bends sideways) and io->t.
- static variables are allowed for state. No malloc, no printf, no other libc.
- The body's "notes" field says how the body is mounted, its frame of
  reference, and what is known about it. Trust it over your assumptions.
- If the body has a "library": those skills already worked on this body and
  their params were tuned by a person watching. Start from the best one —
  keep its structure and its param names/order, make the tuned values the
  defaults (used when a param is 0), and improve from there. Say so in "idea".
  A library entry may instead carry "text": documents an agent fetched from
  the project's knowledge about skills for this body; read them the same way.
- If a direction is unknown (for example which sign of a joint angle bends a
  finger toward the palm), expose it as a param with range -1..1 and use its
  sign, so feedback can flip it.`

const user = `Body (discovered by the robot itself by wiggling each joint):
${JSON.stringify(body, null, 2)}

Intent: ${intent}
${feedback ? `\nPrevious attempt result (improve on it): ${feedback}` : ''}`

;(async () => {
  if (process.env.POKE_LLM === 'cli') return viaCli()
  let client
  try { client = makeClient() } catch (e) { return viaCli() }
  let msg
  try { msg = await client.messages.stream({
    model: process.env.POKE_MODEL || 'claude-sonnet-5',
    max_tokens: 32000,
    system,
    messages: [{ role: 'user', content: user }],
  }).finalMessage() } catch (e) {
    if (/credit balance|authentication|api key/i.test(String(e.message))) return viaCli()
    throw e
  }
  finish(textOf(msg), msg.stop_reason)
})().catch((e) => { console.error(e.message); process.exit(1) })

/* Claude Code CLI instead of the API: the person's own Claude login, no API credit. */
function viaCli() {
  const { execFileSync } = require('child_process')
  const out = JSON.parse(execFileSync(process.env.POKE_CLAUDE || 'claude',
    ['-p', user, '--system-prompt', system, '--tools', '', '--output-format', 'json', '--no-session-persistence'],
    { encoding: 'utf8', maxBuffer: 16 << 20, cwd: require('os').tmpdir(),
      env: Object.fromEntries(Object.entries(process.env).filter(([k]) => !/^ANTHROPIC_(API_KEY|AUTH_TOKEN)$/.test(k))) }))
  if (out.is_error) { console.error('claude -p: ' + String(out.result).slice(0, 300)); process.exit(1) }
  finish(String(out.result || '').trim().replace(/^```\w*\n?/, '').replace(/\n?```$/, ''), 'end_turn')
}

function finish(code, stopReason) {
  if (!code) { console.error(`no text (stop_reason=${stopReason})`); process.exit(2) }
  const m = code.split('\n')[0].match(/SKILL-META\s+(\{.*\})/)
  if (!m) { console.error('missing SKILL-META line'); fs.writeFileSync(outPath, code + '\n'); process.exit(3) }
  fs.writeFileSync(outPath, code + '\n')
  process.stdout.write(m[1] + '\n')
}
