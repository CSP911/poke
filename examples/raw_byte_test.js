/**
 * POKE Raw Byte Test — LLM이 컴파일러 없이 직접 기계어를 생성하는 능력 테스트
 *
 * 모델별로 테스트하고 정답률을 측정합니다.
 */

require('dotenv').config()
const http = require('http')
const Anthropic = require('@anthropic-ai/sdk')

const client = new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY })
const POKE_ENDPOINT = 'http://localhost:8080'

// ── 테스트 케이스 (Easy / Medium / Hard) ──
const TESTS = [
  // ── Easy: single instruction ──
  { task: 'return 0', expected: 0, difficulty: 'easy' },
  { task: 'return 42', expected: 42, difficulty: 'easy' },
  { task: 'return 0xDEAD', expected: 0xDEAD, difficulty: 'easy' },
  { task: '2 + 3', expected: 5, difficulty: 'easy' },
  { task: '100 - 37', expected: 63, difficulty: 'easy' },
  { task: '10 * 7', expected: 70, difficulty: 'easy' },

  // ── Medium: multi-instruction ──
  { task: '50 / 2 (integer division)', expected: 25, difficulty: 'medium' },
  { task: '255 AND 0x0F (bitwise AND)', expected: 15, difficulty: 'medium' },
  { task: '0xF0 OR 0x0F (bitwise OR)', expected: 255, difficulty: 'medium' },
  { task: 'shift 1 left by 8 bits', expected: 256, difficulty: 'medium' },
  { task: 'shift 1024 right by 3 bits', expected: 128, difficulty: 'medium' },
  { task: 'negate -5 (return positive 5)', expected: 5, difficulty: 'medium' },
  { task: 'compute (3 + 7) * 2', expected: 20, difficulty: 'medium' },
  { task: 'compute 100 - 30 - 20 - 10', expected: 40, difficulty: 'medium' },

  // ── Hard: complex logic ──
  { task: 'compute 2 to the power of 10 (use shifts)', expected: 1024, difficulty: 'hard' },
  { task: 'compute factorial of 5 (5! = 120) using a loop', expected: 120, difficulty: 'hard' },
  { task: 'sum integers from 1 to 10 using a loop', expected: 55, difficulty: 'hard' },
  { task: 'compute fibonacci(10) using a loop (fib(0)=0, fib(1)=1)', expected: 55, difficulty: 'hard' },
  { task: 'count how many bits are set in 0xFF (should be 8)', expected: 8, difficulty: 'hard' },
  { task: 'return the larger of 37 and 92 (use comparison)', expected: 92, difficulty: 'hard' },
]

// ── 엣지에 raw 바이트 전송 ──
function pokeRaw(hexBytes) {
  return new Promise((resolve) => {
    const buf = Buffer.from(hexBytes.replace(/\s+/g, ''), 'hex')
    const req = http.request({
      hostname: 'localhost', port: 8080, path: '/poke', method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': buf.length },
      timeout: 5000,
    }, (res) => {
      let d = ''; res.on('data', c => d += c); res.on('end', () => resolve(d.trim()))
    })
    req.on('error', () => resolve(null))
    req.on('timeout', () => { req.destroy(); resolve(null) })
    req.write(buf); req.end()
  })
}

// ── LLM에게 raw 바이트 요청 ──
async function askForBytes(model, task) {
  const msg = await client.messages.create({
    model,
    max_tokens: 300,
    system: `You are an x86 (i386, 32-bit) machine code generator.

Given a task, output ONLY the raw hex bytes of x86-32 machine code that performs the task.
The code is called as a function (CALL instruction). Return result in EAX. End with RET (C3).

Rules:
- Output ONLY hex bytes separated by spaces. Nothing else. No explanation.
- 32-bit mode (not 16-bit, not 64-bit)
- Example: "return 42" → "B8 2A 00 00 00 C3"
  (B8 = mov eax, imm32; 2A000000 = 42 in little-endian; C3 = ret)
- Example: "2 + 3" → "B8 02 00 00 00 83 C0 03 C3"
  (mov eax,2; add eax,3; ret)
- Common opcodes: B8=mov eax,imm32  83 C0=add eax,imm8  83 E8=sub eax,imm8
  69 C0=imul eax,eax,imm32  25=and eax,imm32  C1 E0=shl eax,imm8
  99/F7 F9=cdq+idiv ecx  B9=mov ecx,imm32  C3=ret`,
    messages: [{ role: 'user', content: task }],
  })

  let text = msg.content[0].type === 'text' ? msg.content[0].text.trim() : ''
  // 마크다운이나 설명 제거
  text = text.replace(/```[^\n]*\n?/g, '').replace(/\n/g, ' ').trim()
  // hex 바이트만 추출
  const hexMatch = text.match(/([0-9A-Fa-f]{2}(\s+[0-9A-Fa-f]{2})*)/g)
  return hexMatch ? hexMatch.join(' ') : text
}

// ── nasm으로 정답 바이트 생성 (검증용) ──
function nasmBytes(task) {
  const { execSync } = require('child_process')
  const fs = require('fs')
  // 단순 비교용으로 nasm 결과도 생성
  return null // 이 테스트에서는 실행 결과로만 판정
}

// ── 메인 ──
async function main() {
  const models = [
    'claude-opus-4-8',
    'claude-opus-4-6',
    'claude-haiku-4-5-20251001',
  ]

  console.log('═══════════════════════════════════════════')
  console.log('  POKE Raw Byte Test — LLM = Compiler?')
  console.log('═══════════════════════════════════════════\n')

  for (const model of models) {
    console.log(`\n── Model: ${model} ──\n`)
    let pass = 0, fail = 0, error = 0

    const byDifficulty = { easy: { pass: 0, total: 0 }, medium: { pass: 0, total: 0 }, hard: { pass: 0, total: 0 } }
    let currentDiff = ''

    for (const test of TESTS) {
      if (test.difficulty !== currentDiff) {
        currentDiff = test.difficulty
        console.log(`\n  [${currentDiff.toUpperCase()}]`)
      }

      const label = test.task.padEnd(55)
      process.stdout.write(`  ${label} `)
      byDifficulty[test.difficulty].total++

      try {
        const hex = await askForBytes(model, test.task)
        const hexShort = hex.length > 40 ? hex.slice(0, 40) + '...' : hex

        const result = await pokeRaw(hex)

        if (!result) {
          console.log(`EXEC FAIL  [${hexShort}]`)
          error++
          continue
        }

        const match = result.match(/eax=(\d+)/)
        if (!match) {
          console.log(`PARSE FAIL [${hexShort}]`)
          error++
          continue
        }

        const got = parseInt(match[1])
        if (got === test.expected) {
          console.log(`= ${got} ✅`)
          pass++
          byDifficulty[test.difficulty].pass++
        } else {
          console.log(`= ${got} ❌ (expected ${test.expected}) [${hexShort}]`)
          fail++
        }
      } catch (e) {
        console.log(`ERROR: ${e.message.slice(0, 60)}`)
        error++
      }
    }

    const total = TESTS.length
    const pct = ((pass / total) * 100).toFixed(0)
    console.log(`\n  ┌─────────────────────────────────────┐`)
    console.log(`  │ Total:   ${String(pass).padStart(2)}/${total} (${pct}%)${' '.repeat(20 - pct.length)}│`)
    console.log(`  │ Easy:    ${byDifficulty.easy.pass}/${byDifficulty.easy.total}${' '.repeat(25)}│`)
    console.log(`  │ Medium:  ${byDifficulty.medium.pass}/${byDifficulty.medium.total}${' '.repeat(25)}│`)
    console.log(`  │ Hard:    ${byDifficulty.hard.pass}/${byDifficulty.hard.total}${' '.repeat(25)}│`)
    console.log(`  │ Errors:  ${error}${' '.repeat(27)}│`)
    console.log(`  └─────────────────────────────────────┘`)
  }

  console.log('\n═══════════════════════════════════════════')
}

main().catch(console.error)
