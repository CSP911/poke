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

// ── 테스트 케이스 ──
const TESTS = [
  { task: '2 + 3을 계산해라', expected: 5 },
  { task: '10 * 7을 계산해라', expected: 70 },
  { task: '100 - 37을 계산해라', expected: 63 },
  { task: '255 AND 0x0F (비트 연산)', expected: 15 },
  { task: '1을 왼쪽으로 8번 시프트해라', expected: 256 },
  { task: '50을 2로 나눠라 (정수 나눗셈)', expected: 25 },
  { task: '0xDEAD를 리턴해라', expected: 0xDEAD },
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
    'claude-haiku-4-5-20251001',
    'claude-opus-4-6',
  ]

  console.log('═══════════════════════════════════════════')
  console.log('  POKE Raw Byte Test — LLM = Compiler?')
  console.log('═══════════════════════════════════════════\n')

  for (const model of models) {
    console.log(`\n── Model: ${model} ──\n`)
    let pass = 0, fail = 0, error = 0

    for (const test of TESTS) {
      process.stdout.write(`  "${test.task}" → `)

      try {
        const hex = await askForBytes(model, test.task)
        process.stdout.write(`[${hex}] → `)

        // 엣지에서 실행
        const result = await pokeRaw(hex)

        if (!result) {
          console.log('EXEC FAIL')
          error++
          continue
        }

        // eax= 파싱
        const match = result.match(/eax=(\d+)/)
        if (!match) {
          console.log(`PARSE FAIL (${result})`)
          error++
          continue
        }

        const got = parseInt(match[1])
        if (got === test.expected) {
          console.log(`${got} ✅`)
          pass++
        } else {
          console.log(`${got} ❌ (expected ${test.expected})`)
          fail++
        }
      } catch (e) {
        console.log(`ERROR: ${e.message}`)
        error++
      }
    }

    const total = TESTS.length
    const pct = ((pass / total) * 100).toFixed(0)
    console.log(`\n  Result: ${pass}/${total} passed (${pct}%) | ${fail} wrong | ${error} errors`)
  }

  console.log('\n═══════════════════════════════════════════')
}

main().catch(console.error)
