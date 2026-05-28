/**
 * POKE External Data Test
 *
 * 바이너리가 외부 데이터를 참조하는 3가지 패턴:
 *   1. 데이터 내장 (코드 뒤에 데이터 붙이기)
 *   2. 허브가 fetch → 데이터를 바이너리에 주입
 *   3. 여러 도시 날씨 → 평균/최대/최소 계산
 */

const http = require('http')
const { assemble } = require('./asm.js')

function pokeExec(port, bin) {
  return new Promise((resolve) => {
    const req = http.request({
      hostname: 'localhost', port, path: '/poke', method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': bin.length },
      timeout: 10000,
    }, (res) => {
      let d = ''; res.on('data', c => d += c)
      res.on('end', () => resolve(d.trim()))
    })
    req.on('error', e => resolve('error: ' + e.message))
    req.write(bin); req.end()
  })
}

function fetchJSON(url) {
  return new Promise((resolve) => {
    const mod = url.startsWith('https') ? require('https') : require('http')
    mod.get(url, { headers: { 'User-Agent': 'curl/7.0' }, timeout: 10000 }, (res) => {
      let d = ''; res.on('data', c => d += c); res.on('end', () => {
        try { resolve(JSON.parse(d)) } catch { resolve(null) }
      })
    }).on('error', () => resolve(null))
  })
}

async function main() {
  console.log('═══════════════════════════════════════════')
  console.log('  POKE External Data → Binary Test')
  console.log('═══════════════════════════════════════════\n')

  // ──────────────────────────────────
  // Test 1: 배열 데이터를 바이너리에 내장
  // Hub embeds data directly into assembly instructions.
  // ──────────────────────────────────
  console.log('── Test 1: 배열 합산 (데이터 내장) ──')

  const numbers = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
  console.log(`   데이터: [${numbers.join(', ')}]`)

  // Hub embeds data as immediate values in assembly
  const embedAsm = numbers.map((n, i) =>
    i === 0 ? `mov eax, ${n}` : `add eax, ${n}`
  ).join('\n') + '\nret'

  const embedBin = assemble(embedAsm)
  const r1 = await pokeExec(8080, embedBin)
  console.log(`   코드: ${embedAsm.replace(/\n/g, ' | ')}`)
  console.log(`   결과: ${r1}  (${embedBin.length}B)`)
  console.log(`   기대: ${numbers.reduce((a, b) => a + b, 0)}\n`)

  // ──────────────────────────────────
  // Test 2: 큰 데이터 → 메모리에 쓰고 코드가 참조
  // code_buf 뒤쪽에 데이터를 위치시킴
  // ──────────────────────────────────
  console.log('── Test 2: 1000개 숫자 합산 (바이너리에 데이터 섹션) ──')

  // 1~1000 배열
  const bigData = Array.from({ length: 1000 }, (_, i) => i + 1)
  const expected = bigData.reduce((a, b) => a + b, 0)  // 500500

  // 방법: 바이너리 = [코드] + [데이터]
  // 코드가 call/pop으로 자기 주소를 알아낸 뒤, 데이터 오프셋으로 접근
  //
  // 구조:
  //   offset 0:  call next          (5 bytes)
  //   offset 5:  next: pop ebx      (1 byte) → ebx = addr of offset 5
  //              ... code ...
  //   offset N:  ret                 (1 byte)
  //   offset N+1: [data: 1000 u32s]
  //
  // 데이터 접근: [ebx + (N+1 - 5) + ecx*4]
  //            = [ebx + CODE_SIZE_AFTER_POP + ecx*4]

  // 코드 부분을 먼저 어셈블하되, 데이터 참조를 수동으로 인코딩

  // 코드 (수동 바이트 생성):
  const code = []

  // call next (E8 00 00 00 00)
  code.push(0xE8, 0x00, 0x00, 0x00, 0x00)
  // pop ebx (5B)
  code.push(0x5B)
  // → ebx = address of this pop instruction (offset 5 in code_buf)

  // xor eax, eax (31 C0)
  code.push(0x31, 0xC0)
  // xor ecx, ecx (31 C9)
  code.push(0x31, 0xC9)

  // loop: cmp ecx, 1000 (81 F9 E8030000)
  const loopAddr = code.length  // relative to start
  code.push(0x81, 0xF9); pushLE32(code, 1000)

  // jge done (7D XX — placeholder)
  code.push(0x7D)
  const jgeFixup = code.length
  code.push(0x00)

  // mov edx, [ebx + ecx*4 + OFFSET]
  // OFFSET = (code끝 - pop위치) = (ret이후 - 5)
  // 이건 코드 완성 후에 알 수 있음 → placeholder
  // [ebx + ecx*4 + disp32] encoding:
  // 8B 94 8B disp32  (mov edx, [ebx + ecx*4 + disp32])
  code.push(0x8B, 0x94, 0x8B)
  const dispFixup = code.length
  code.push(0x00, 0x00, 0x00, 0x00) // placeholder for disp32

  // add eax, edx (01 D0)
  code.push(0x01, 0xD0)

  // inc ecx (41)
  code.push(0x41)

  // jmp loop (EB XX)
  code.push(0xEB)
  const jmpOffset = code.length
  code.push(loopAddr - (code.length + 1))  // relative jump back

  // done: ret (C3)
  const doneAddr = code.length
  code.push(0xC3)

  // fix jge offset
  code[jgeFixup] = doneAddr - (jgeFixup + 1)

  // 데이터 시작 = code.length, pop은 offset 5
  // disp32 = code.length - 5 (데이터 시작 relative to ebx)
  const dataOffset = code.length - 5
  code[dispFixup] = dataOffset & 0xFF
  code[dispFixup + 1] = (dataOffset >> 8) & 0xFF
  code[dispFixup + 2] = (dataOffset >> 16) & 0xFF
  code[dispFixup + 3] = (dataOffset >> 24) & 0xFF

  // 데이터 섹션: 1000개 u32 (little-endian)
  for (const n of bigData) {
    pushLE32(code, n)
  }

  const bigBin = Buffer.from(code)
  console.log(`   데이터: [1, 2, 3, ..., 1000] (${bigData.length}개)`)
  console.log(`   바이너리: ${bigBin.length}B (코드 ${doneAddr + 1}B + 데이터 ${bigData.length * 4}B)`)

  const r2 = await pokeExec(8080, bigBin)
  console.log(`   결과: ${r2}`)
  console.log(`   기대: eax=${expected}\n`)

  // ──────────────────────────────────
  // Test 3: 외부 API → 데이터 → 바이너리 → 실행
  // 여러 도시 날씨를 가져와서 평균 기온 계산
  // ──────────────────────────────────
  console.log('── Test 3: 실제 날씨 API → 바이너리에 주입 → 평균 계산 ──')

  const cities = ['Seoul', 'Tokyo', 'Beijing', 'Bangkok']
  const temps = []

  for (const city of cities) {
    process.stdout.write(`   ${city}: fetching... `)
    const data = await fetchJSON(`http://wttr.in/${city}?format=j1`)
    const temp = data?.current_condition?.[0]?.temp_C
    if (temp) {
      temps.push({ city, temp: parseInt(temp) })
      console.log(`${temp}°C`)
    } else {
      console.log('failed')
    }
  }

  if (temps.length > 0) {
    console.log(`\n   데이터: [${temps.map(t => t.temp).join(', ')}]`)

    // 평균 계산 바이너리 생성
    // 합계 구하기 → 개수로 나누기
    const tempAsm = [
      ...temps.map((t, i) =>
        i === 0 ? `mov eax, ${t.temp}` : `add eax, ${t.temp}`
      ),
      `mov ecx, ${temps.length}`,
      'cdq',
      'idiv ecx',
      'ret',
    ].join('\n')

    const tempBin = assemble(tempAsm)
    const r3 = await pokeExec(8080, tempBin)
    const avg = Math.floor(temps.reduce((a, t) => a + t.temp, 0) / temps.length)
    console.log(`   바이너리: ${tempAsm.replace(/\n/g, ' | ')}`)
    console.log(`   결과: ${r3}  (${tempBin.length}B)`)
    console.log(`   검증: 평균 = ${avg}°C`)

    // min/max도 계산
    const minAsm = [
      `mov eax, ${temps[0].temp}`,
      ...temps.slice(1).map(t => [
        `cmp eax, ${t.temp}`,
        `jle skip_${t.city}`,
        `mov eax, ${t.temp}`,
        `skip_${t.city}:`,
      ].join('\n')),
      'ret',
    ].join('\n')

    const maxAsm = [
      `mov eax, ${temps[0].temp}`,
      ...temps.slice(1).map(t => [
        `cmp eax, ${t.temp}`,
        `jge skip_${t.city}`,
        `mov eax, ${t.temp}`,
        `skip_${t.city}:`,
      ].join('\n')),
      'ret',
    ].join('\n')

    const [rMin, rMax] = await Promise.all([
      pokeExec(8080, assemble(minAsm)),
      pokeExec(8082, assemble(maxAsm)),  // 병렬로 다른 엣지에서!
    ])

    console.log(`\n   최저: ${rMin} (edge-1)`)
    console.log(`   최고: ${rMax} (edge-2, 병렬 실행!)`)
  }

  console.log('\n═══════════════════════════════════════════')
  console.log('  외부 데이터 → 바이너리 주입 → 베어메탈 실행')
  console.log('  컴파일러 사용: 0  |  외부 의존성: 0')
  console.log('═══════════════════════════════════════════')
}

function pushLE32(arr, val) {
  val = val >>> 0
  arr.push(val & 0xFF, (val >> 8) & 0xFF, (val >> 16) & 0xFF, (val >> 24) & 0xFF)
}

main().catch(console.error)
