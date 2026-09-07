/* CLI-side LLM access — .env loading + Anthropic client */
const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '../..')

function loadEnv() {
  try {
    for (const line of fs.readFileSync(path.join(ROOT, '.env'), 'utf8').split('\n')) {
      const m = line.match(/^([A-Z_]+)=(.*)$/)
      if (m && !process.env[m[1]]) process.env[m[1]] = m[2]
    }
  } catch {}
}

function makeClient() {
  loadEnv()
  if (!process.env.ANTHROPIC_API_KEY) throw new Error('ANTHROPIC_API_KEY not set (.env)')
  const Anthropic = require(path.join(ROOT, 'node_modules/@anthropic-ai/sdk'))
  return new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY })
}

function textOf(msg) {
  const block = msg.content.find((b) => b.type === 'text')
  return block ? block.text.trim().replace(/^```\w*\n?/, '').replace(/\n?```$/, '') : null
}

module.exports = { ROOT, loadEnv, makeClient, textOf }
