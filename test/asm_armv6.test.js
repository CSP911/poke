/**
 * POKE Mini Assembler — ARMv6 (asm_armv6.js) — automated tests
 *
 * Usage: node test/asm_armv6.test.js
 */

const { assembleARMv6, scanBinaryARMv6, assembleAndValidateARMv6, encodeImm, MAX_BINARY_SIZE } = require('../hub/assembler/asm_armv6.js')

let passed = 0
let failed = 0

function assert(cond, msg) {
  if (cond) { passed++; console.log(`  ✓ ${msg}`) }
  else { failed++; console.error(`  ✗ ${msg}`) }
}

function expectWords(name, code, expectedWords) {
  try {
    const buf = assembleARMv6(code)
    const words = []
    for (let i = 0; i < buf.length; i += 4) {
      words.push(buf.readUInt32LE(i))
    }
    const ok = words.length === expectedWords.length &&
      words.every((w, i) => (w >>> 0) === (expectedWords[i] >>> 0))
    if (!ok) {
      console.error(`  ✗ ${name}`)
      console.error(`    expected: [${expectedWords.map(w => '0x' + (w >>> 0).toString(16).toUpperCase().padStart(8, '0')).join(', ')}]`)
      console.error(`    got:      [${words.map(w => '0x' + (w >>> 0).toString(16).toUpperCase().padStart(8, '0')).join(', ')}]`)
      failed++
    } else {
      passed++
      console.log(`  ✓ ${name}`)
    }
  } catch (e) {
    failed++
    console.error(`  ✗ ${name} — threw: ${e.message}`)
  }
}

function expectLen(name, code, expectedLen) {
  try {
    const buf = assembleARMv6(code)
    assert(buf.length === expectedLen, `${name} (${buf.length} bytes)`)
  } catch (e) {
    failed++
    console.error(`  ✗ ${name} — threw: ${e.message}`)
  }
}

function expectThrow(name, code) {
  try {
    assembleARMv6(code)
    failed++
    console.error(`  ✗ ${name} — expected throw, but succeeded`)
  } catch (e) {
    passed++
    console.log(`  ✓ ${name} (threw: ${e.message})`)
  }
}

// ══════════════════════════════════════
console.log('\n── Immediate Encoding ──')

assert(encodeImm(0) !== null, 'encodeImm(0)')
assert(encodeImm(0xFF) !== null, 'encodeImm(0xFF)')
assert(encodeImm(0x100) !== null, 'encodeImm(0x100)')  // 1 << 8
assert(encodeImm(0x200) !== null, 'encodeImm(0x200)')
assert(encodeImm(0xFF000000) !== null, 'encodeImm(0xFF000000)')
assert(encodeImm(0x102) === null, 'encodeImm(0x102) = null (not representable)')

// ══════════════════════════════════════
console.log('\n── Data Movement ──')

// mov r0, #42 → 0xE3A0002A
// bx lr → 0xE12FFF1E
expectWords('mov r0, #42; bx lr',
  'mov r0, #42\nbx lr',
  [0xE3A0002A, 0xE12FFF1E])

// mov r0, r1 → 0xE1A00001
expectWords('mov r0, r1',
  'mov r0, r1',
  [0xE1A00001])

// mov r0, #0 → 0xE3A00000
expectWords('mov r0, #0',
  'mov r0, #0',
  [0xE3A00000])

// mov r0, #0xFF → 0xE3A000FF
expectWords('mov r0, #255',
  'mov r0, #255',
  [0xE3A000FF])

// mvn r0, #0 → NOT(0) = 0xFFFFFFFF → 0xE3E00000
expectWords('mvn r0, #0',
  'mvn r0, #0',
  [0xE3E00000])

// ══════════════════════════════════════
console.log('\n── Arithmetic ──')

// add r0, r1, r2 → 0xE0810002
expectWords('add r0, r1, r2',
  'add r0, r1, r2',
  [0xE0810002])

// sub r0, r1, #10 → 0xE241000A
expectWords('sub r0, r1, #10',
  'sub r0, r1, #10',
  [0xE241000A])

// add r0, r0, #1 → 0xE2800001
expectWords('add r0, r0, #1',
  'add r0, r0, #1',
  [0xE2800001])

// rsb r0, r1, #0 → negate: 0xE2610000
expectWords('rsb r0, r1, #0',
  'rsb r0, r1, #0',
  [0xE2610000])

// mul r0, r1, r2 → 0xE0000291
expectWords('mul r0, r1, r2',
  'mul r0, r1, r2',
  [0xE0000291])

// ══════════════════════════════════════
console.log('\n── Logic ──')

// and r0, r1, r2 → 0xE0010002
expectWords('and r0, r1, r2',
  'and r0, r1, r2',
  [0xE0010002])

// orr r0, r1, r2 → 0xE1810002
expectWords('orr r0, r1, r2',
  'orr r0, r1, r2',
  [0xE1810002])

// eor r0, r1, r2 → 0xE0210002
expectWords('eor r0, r1, r2',
  'eor r0, r1, r2',
  [0xE0210002])

// bic r0, r1, r2 → 0xE1C10002
expectWords('bic r0, r1, r2',
  'bic r0, r1, r2',
  [0xE1C10002])

// ══════════════════════════════════════
console.log('\n── Shifts ──')

// lsl r0, r1, #2 → mov r0, r1, lsl #2 → 0xE1A00101
expectWords('lsl r0, r1, #2',
  'lsl r0, r1, #2',
  [0xE1A00101])

// lsr r0, r1, #4 → 0xE1A00221
expectWords('lsr r0, r1, #4',
  'lsr r0, r1, #4',
  [0xE1A00221])

// asr r0, r1, #8 → 0xE1A00441
expectWords('asr r0, r1, #8',
  'asr r0, r1, #8',
  [0xE1A00441])

// ══════════════════════════════════════
console.log('\n── Compare ──')

// cmp r0, #10 → 0xE350000A
expectWords('cmp r0, #10',
  'cmp r0, #10',
  [0xE350000A])

// cmp r0, r1 → 0xE1500001
expectWords('cmp r0, r1',
  'cmp r0, r1',
  [0xE1500001])

// tst r0, r1 → 0xE1100001
expectWords('tst r0, r1',
  'tst r0, r1',
  [0xE1100001])

// ══════════════════════════════════════
console.log('\n── Load/Store ──')

// ldr r0, [r1] → 0xE5910000
expectWords('ldr r0, [r1]',
  'ldr r0, [r1]',
  [0xE5910000])

// ldr r0, [r1, #4] → 0xE5910004
expectWords('ldr r0, [r1, #4]',
  'ldr r0, [r1, #4]',
  [0xE5910004])

// str r0, [r1, #8] → 0xE5810008
expectWords('str r0, [r1, #8]',
  'str r0, [r1, #8]',
  [0xE5810008])

// ldrb r0, [r1] → 0xE5D10000
expectWords('ldrb r0, [r1]',
  'ldrb r0, [r1]',
  [0xE5D10000])

// str r2, [r0] → 0xE5802000
expectWords('str r2, [r0]',
  'str r2, [r0]',
  [0xE5802000])

// ldr with negative offset: ldr r0, [r1, #-4] → 0xE5110004
expectWords('ldr r0, [r1, #-4]',
  'ldr r0, [r1, #-4]',
  [0xE5110004])

// ══════════════════════════════════════
console.log('\n── Literal Pool (ldr Rd, =value) ──')

// ldr r0, =0x20200000; bx lr
// Should produce: LDR r0, [PC, #0], BX LR, .word 0x20200000
{
  const buf = assembleARMv6('ldr r0, =0x20200000\nbx lr')
  assert(buf.length === 12, 'ldr =0x20200000 produces 12 bytes (3 words)')
  const pool = buf.readUInt32LE(8)
  assert(pool === 0x20200000, `literal pool value = 0x${pool.toString(16)}`)
}

// ldr with small value should use MOV instead of pool
{
  const buf = assembleARMv6('ldr r0, =42\nbx lr')
  assert(buf.length === 8, 'ldr =42 uses MOV encoding (8 bytes, no pool)')
}

// Multiple ldr = same value shares pool slot
{
  const buf = assembleARMv6('ldr r0, =0x20200000\nldr r1, =0x20200000\nbx lr')
  assert(buf.length === 16, 'two ldr same value share one pool slot (16 bytes)')
}

// Multiple ldr = different values
{
  const buf = assembleARMv6('ldr r0, =0x20200000\nldr r1, =0x20215000\nbx lr')
  assert(buf.length === 20, 'two ldr different values = 2 pool slots (20 bytes)')
}

// ══════════════════════════════════════
console.log('\n── Push/Pop ──')

// push {r4, lr} → 0xE92D4010
expectWords('push {r4, lr}',
  'push {r4, lr}',
  [0xE92D4010])

// pop {r4, pc} → 0xE8BD8010
expectWords('pop {r4, pc}',
  'pop {r4, pc}',
  [0xE8BD8010])

// push {r0-r3} → 0xE92D000F
expectWords('push {r0-r3}',
  'push {r0-r3}',
  [0xE92D000F])

// ══════════════════════════════════════
console.log('\n── Branches ──')

// b label (forward)
{
  const buf = assembleARMv6('b skip\nmov r0, #1\nskip:\nmov r0, #2\nbx lr')
  assert(buf.length === 16, 'b forward skip produces 4 instructions')
  const brWord = buf.readUInt32LE(0)
  assert(((brWord & 0xFF000000) >>> 0) === (0xEA000000 >>> 0), 'b instruction has correct opcode')
}

// bne label (backward loop)
{
  const buf = assembleARMv6('mov r0, #0\nloop:\nadd r0, r0, #1\ncmp r0, #10\nbne loop\nbx lr')
  assert(buf.length === 20, 'loop with bne produces 5 instructions')
  const bneWord = buf.readUInt32LE(12)
  assert((bneWord & 0xFF000000) === 0x1A000000, 'bne has condition NE (0x1)')
}

// bl label (function call)
{
  const buf = assembleARMv6('bl func\nbx lr\nfunc:\nmov r0, #99\nbx lr')
  assert(buf.length === 16, 'bl produces 4 instructions')
  const blWord = buf.readUInt32LE(0)
  assert(((blWord & 0xFF000000) >>> 0) === (0xEB000000 >>> 0), 'bl instruction has correct opcode')
}

// beq label
{
  const buf = assembleARMv6('cmp r0, #0\nbeq done\nmov r0, #1\ndone:\nbx lr')
  assert(buf.length === 16, 'beq produces 4 instructions')
  const beqWord = buf.readUInt32LE(4)
  assert((beqWord & 0xFF000000) === 0x0A000000, 'beq has condition EQ (0x0)')
}

// ══════════════════════════════════════
console.log('\n── NOP ──')

expectWords('nop', 'nop', [0xE1A00000])

// ══════════════════════════════════════
console.log('\n── Special Registers ──')

// mov sp, r0
expectWords('mov sp, r0',
  'mov sp, r0',
  [0xE1A0D000])

// mov lr, pc
expectWords('mov lr, pc',
  'mov lr, pc',
  [0xE1A0E00F])

// ══════════════════════════════════════
console.log('\n── Comments & Directives ──')

{
  const buf = assembleARMv6('mov r0, #1  @ comment\n.text\nmov r1, #2 // another\nbx lr')
  assert(buf.length === 12, 'comments and directives stripped')
}

// ══════════════════════════════════════
console.log('\n── Safety Scanner ──')

// Valid binary ending with BX LR
{
  const { binary, scan } = assembleAndValidateARMv6('mov r0, #42\nbx lr')
  assert(scan.safe, 'valid binary is safe')
  assert(scan.warnings.length === 0, 'no warnings for valid binary')
}

// Binary without return
{
  const buf = assembleARMv6('mov r0, #42')
  const scan = scanBinaryARMv6(Array.from(buf))
  assert(scan.warnings.length > 0, 'warns about missing return')
}

// SWI blocked
{
  try {
    assembleAndValidateARMv6('swi #0\nbx lr')
    failed++
    console.error('  ✗ SWI should be blocked by scanner')
  } catch (e) {
    assert(e.scan && !e.scan.safe, 'SWI blocked by safety scanner')
  }
}

// POP {pc} is valid return
{
  const buf = assembleARMv6('push {lr}\nmov r0, #42\npop {pc}')
  const scan = scanBinaryARMv6(Array.from(buf))
  assert(scan.warnings.length === 0, 'POP {pc} recognized as valid return')
}

// ══════════════════════════════════════
console.log('\n── Real-world Patterns (Pi Zero W GPIO) ──')

// GPIO read pattern: ldr r0, =GPIO_BASE; ldr r0, [r0]; bx lr
{
  const buf = assembleARMv6('ldr r0, =0x20200000\nldr r0, [r0]\nbx lr')
  assert(buf.length === 16, 'GPIO read pattern produces 4 words (incl pool)')
  const poolVal = buf.readUInt32LE(12)
  assert(poolVal === 0x20200000, 'pool has GPIO_BASE address')
}

// GPIO write pattern
{
  const code = `
    ldr r0, =0x20200000
    ldr r1, =0x00200000
    str r1, [r0, #28]
    bx lr
  `
  const buf = assembleARMv6(code)
  assert(buf.length >= 20, 'GPIO write pattern compiles')
}

// Arithmetic + return pattern
{
  const code = `
    push {r4, lr}
    mov r4, r0
    add r0, r4, #100
    mul r0, r0, r4
    pop {r4, pc}
  `
  const { binary, scan } = assembleAndValidateARMv6(code)
  assert(scan.safe, 'arithmetic pattern passes safety scan')
  assert(binary.length === 20, 'arithmetic pattern = 5 instructions')
}

// ══════════════════════════════════════
console.log('\n── Error Cases ──')

expectThrow('unknown instruction', 'INVALID r0, r1')
expectThrow('bad register', 'mov r99, #1')
expectThrow('undefined label', 'b nowhere')
expectThrow('unrepresentable immediate', 'mov r0, #0x102')

// ══════════════════════════════════════
console.log(`\n═══ Results: ${passed} passed, ${failed} failed ═══\n`)
process.exit(failed > 0 ? 1 : 0)
