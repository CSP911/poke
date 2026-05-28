/**
 * POKE Parallel Execution Test
 *
 * 같은 계산을 두 가지 방식으로 실행하고 시간 비교:
 *   1. 단일 노드: 하나의 바이너리로 순차 실행
 *   2. 병렬 노드: 데이터 독립적인 부분을 여러 엣지에 분산
 */

const http = require('http')
const { assemble } = require('./asm.js')

const EDGES = [
  { id: 'edge-1', port: 8080 },
  { id: 'edge-2', port: 8082 },
]

// ── 엣지에 바이너리 전송 + 실행 ──
function pokeExec(port, bin) {
  return new Promise((resolve) => {
    const start = Date.now()
    const req = http.request({
      hostname: 'localhost', port, path: '/poke', method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': bin.length },
      timeout: 10000,
    }, (res) => {
      let d = ''; res.on('data', c => d += c)
      res.on('end', () => {
        const elapsed = Date.now() - start
        const m = d.match(/eax=(\d+)/)
        resolve({ value: m ? parseInt(m[1]) : null, raw: d.trim(), ms: elapsed })
      })
    })
    req.on('error', e => resolve({ value: null, raw: e.message, ms: Date.now() - start }))
    req.write(bin); req.end()
  })
}

// ══════════════════════════════════════
// 테스트 1: 단순 연산 (독립 곱셈 4개의 합)
// (17 * 31) + (23 * 47) + (13 * 37) + (29 * 43)
// = 527 + 1081 + 481 + 1247 = 3336
// ══════════════════════════════════════

async function test1_single() {
  // 하나의 바이너리에서 순차 실행
  const asm = `
    ; (17*31) + (23*47) + (13*37) + (29*43)
    push ebx
    push esi
    push edi

    mov eax, 17
    imul eax, 31
    mov ebx, eax      ; ebx = 527

    mov eax, 23
    imul eax, 47
    mov esi, eax      ; esi = 1081

    mov eax, 13
    imul eax, 37
    mov edi, eax      ; edi = 481

    mov eax, 29
    imul eax, 43      ; eax = 1247

    add eax, ebx
    add eax, esi
    add eax, edi      ; eax = 3336

    pop edi
    pop esi
    pop ebx
    ret
  `
  const bin = assemble(asm)
  const result = await pokeExec(EDGES[0].port, bin)
  return { ...result, binSize: bin.length }
}

async function test1_parallel() {
  // 4개 곱셈을 2개 엣지에 분산 (각 엣지가 2개씩)
  const asm1 = `
    ; (17*31) + (23*47) = 527 + 1081 = 1608
    mov eax, 17
    imul eax, 31
    mov ecx, eax
    mov eax, 23
    imul eax, 47
    add eax, ecx
    ret
  `
  const asm2 = `
    ; (13*37) + (29*43) = 481 + 1247 = 1728
    mov eax, 13
    imul eax, 37
    mov ecx, eax
    mov eax, 29
    imul eax, 43
    add eax, ecx
    ret
  `
  const bin1 = assemble(asm1)
  const bin2 = assemble(asm2)

  // 동시 실행
  const [r1, r2] = await Promise.all([
    pokeExec(EDGES[0].port, bin1),
    pokeExec(EDGES[1].port, bin2),
  ])

  return {
    value: r1.value + r2.value,
    ms: Math.max(r1.ms, r2.ms),  // wall-clock = 가장 늦은 것
    parts: [r1, r2],
    binSize: bin1.length + bin2.length,
  }
}

// ══════════════════════════════════════
// 테스트 2: 무거운 루프 (CPU bound)
// 각각 100만번 루프 * 4개 = 400만번
// ══════════════════════════════════════

function makeLoopAsm(iterations, addVal) {
  return `
    xor eax, eax
    mov ecx, ${iterations}
  loop_start:
    add eax, ${addVal}
    dec ecx
    jnz loop_start
    ret
  `
}

async function test2_single() {
  // 하나의 엣지에서 4개 루프 순차
  const asm = `
    push ebx
    push esi
    push edi

    ; loop 1: 500000 * 3
    xor eax, eax
    mov ecx, 500000
  l1:
    add eax, 3
    dec ecx
    jnz l1
    mov ebx, eax

    ; loop 2: 500000 * 7
    xor eax, eax
    mov ecx, 500000
  l2:
    add eax, 7
    dec ecx
    jnz l2
    mov esi, eax

    ; loop 3: 500000 * 11
    xor eax, eax
    mov ecx, 500000
  l3:
    add eax, 11
    dec ecx
    jnz l3
    mov edi, eax

    ; loop 4: 500000 * 13
    xor eax, eax
    mov ecx, 500000
  l4:
    add eax, 13
    dec ecx
    jnz l4

    add eax, ebx
    add eax, esi
    add eax, edi

    pop edi
    pop esi
    pop ebx
    ret
  `

  const bin = assemble(asm)
  const result = await pokeExec(EDGES[0].port, bin)
  return { ...result, binSize: bin.length }
}

async function test2_parallel() {
  // 4개 루프를 2개 엣지에 분산
  const asm1 = `
    push ebx
    ; loop 1: 500000 * 3
    xor eax, eax
    mov ecx, 500000
  l1:
    add eax, 3
    dec ecx
    jnz l1
    mov ebx, eax

    ; loop 2: 500000 * 7
    xor eax, eax
    mov ecx, 500000
  l2:
    add eax, 7
    dec ecx
    jnz l2

    add eax, ebx
    pop ebx
    ret
  `
  const asm2 = `
    push ebx
    ; loop 3: 500000 * 11
    xor eax, eax
    mov ecx, 500000
  l3:
    add eax, 11
    dec ecx
    jnz l3
    mov ebx, eax

    ; loop 4: 500000 * 13
    xor eax, eax
    mov ecx, 500000
  l4:
    add eax, 13
    dec ecx
    jnz l4

    add eax, ebx
    pop ebx
    ret
  `

  const bin1 = assemble(asm1)
  const bin2 = assemble(asm2)

  const [r1, r2] = await Promise.all([
    pokeExec(EDGES[0].port, bin1),
    pokeExec(EDGES[1].port, bin2),
  ])

  return {
    value: r1.value + r2.value,
    ms: Math.max(r1.ms, r2.ms),
    parts: [r1, r2],
    binSize: bin1.length + bin2.length,
  }
}

// ══════════════════════════════════════
// 테스트 3: 더 무거운 루프 (차이를 극대화)
// ══════════════════════════════════════

async function test3_single() {
  const asm = `
    push ebx
    push esi

    ; loop 1: 2000000 * 1
    xor eax, eax
    mov ecx, 2000000
  l1:
    inc eax
    dec ecx
    jnz l1
    mov ebx, eax

    ; loop 2: 2000000 * 1
    xor eax, eax
    mov ecx, 2000000
  l2:
    inc eax
    dec ecx
    jnz l2

    add eax, ebx
    pop esi
    pop ebx
    ret
  `
  const bin = assemble(asm)
  return { ...(await pokeExec(EDGES[0].port, bin)), binSize: bin.length }
}

async function test3_parallel() {
  const loopAsm = `
    xor eax, eax
    mov ecx, 2000000
  loop:
    inc eax
    dec ecx
    jnz loop
    ret
  `
  const bin = assemble(loopAsm)

  const [r1, r2] = await Promise.all([
    pokeExec(EDGES[0].port, bin),
    pokeExec(EDGES[1].port, bin),
  ])

  return {
    value: r1.value + r2.value,
    ms: Math.max(r1.ms, r2.ms),
    parts: [r1, r2],
    binSize: bin.length * 2,
  }
}

// ── 메인 ──
async function main() {
  console.log('═══════════════════════════════════════════════')
  console.log('  POKE Parallel Execution Benchmark')
  console.log('  Edge 1: localhost:8080  |  Edge 2: localhost:8082')
  console.log('═══════════════════════════════════════════════\n')

  // 웜업
  const warmBin = assemble('mov eax, 1\nret')
  await Promise.all([pokeExec(8080, warmBin), pokeExec(8082, warmBin)])

  // 테스트 1: 단순 산술
  console.log('── Test 1: 독립 곱셈 4개의 합 ──')
  console.log('   (17*31) + (23*47) + (13*37) + (29*43) = 3336\n')

  const t1s = await test1_single()
  const t1p = await test1_parallel()

  console.log(`   Single:   ${t1s.value}  ${t1s.ms}ms  (${t1s.binSize}B binary)`)
  console.log(`   Parallel: ${t1p.value}  ${t1p.ms}ms  (${t1p.binSize}B across 2 edges)`)
  console.log(`     edge-1: ${t1p.parts[0].value} (${t1p.parts[0].ms}ms)  edge-2: ${t1p.parts[1].value} (${t1p.parts[1].ms}ms)`)
  console.log(`   Speedup:  ${(t1s.ms / t1p.ms).toFixed(2)}x\n`)

  // 테스트 2: 루프 (50만 * 4)
  console.log('── Test 2: 루프 50만회 x 4 (데이터 독립) ──')
  console.log('   sum(500K*3, 500K*7, 500K*11, 500K*13) = 17,000,000\n')

  const t2s = await test2_single()
  const t2p = await test2_parallel()

  console.log(`   Single:   ${t2s.value}  ${t2s.ms}ms  (${t2s.binSize}B binary)`)
  console.log(`   Parallel: ${t2p.value}  ${t2p.ms}ms  (${t2p.binSize}B across 2 edges)`)
  console.log(`     edge-1: ${t2p.parts[0].value} (${t2p.parts[0].ms}ms)  edge-2: ${t2p.parts[1].value} (${t2p.parts[1].ms}ms)`)
  console.log(`   Speedup:  ${(t2s.ms / t2p.ms).toFixed(2)}x\n`)

  // 테스트 3: 무거운 루프 (200만 * 2)
  console.log('── Test 3: 루프 200만회 x 2 ──')
  console.log('   2M + 2M = 4,000,000\n')

  const t3s = await test3_single()
  const t3p = await test3_parallel()

  console.log(`   Single:   ${t3s.value}  ${t3s.ms}ms  (${t3s.binSize}B binary)`)
  console.log(`   Parallel: ${t3p.value}  ${t3p.ms}ms  (${t3p.binSize}B across 2 edges)`)
  console.log(`     edge-1: ${t3p.parts[0].value} (${t3p.parts[0].ms}ms)  edge-2: ${t3p.parts[1].value} (${t3p.parts[1].ms}ms)`)
  console.log(`   Speedup:  ${(t3s.ms / t3p.ms).toFixed(2)}x\n`)

  console.log('═══════════════════════════════════════════════')
  console.log('  모든 바이너리: asm.js로 생성 (nasm 0, gcc 0)')
  console.log('═══════════════════════════════════════════════')
}

main().catch(console.error)
