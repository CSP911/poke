/**
 * POKE Mini Assembler — ARM64 (asm_arm.js) — automated tests
 *
 * Usage: node test/asm_arm.test.js
 */

const { assembleARM, scanBinaryARM, assembleAndValidateARM, MAX_BINARY_SIZE } = require('../asm_arm.js')

let passed = 0
let failed = 0

function assert(cond, msg) {
  if (cond) { passed++; console.log(`  ✓ ${msg}`) }
  else { failed++; console.error(`  ✗ ${msg}`) }
}

function expectBytes(name, code, expectedWords) {
  try {
    const buf = assembleARM(code)
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

function expectThrow(name, code) {
  try {
    assembleARM(code)
    failed++
    console.error(`  ✗ ${name} — expected throw, but succeeded`)
  } catch (e) {
    passed++
    console.log(`  ✓ ${name} (threw: ${e.message})`)
  }
}

// ══════════════════════════════════════
console.log('\n── Data Movement ──')

// mov x0, #42 → MOVZ: 0xD2800000 | (42 << 5) | 0 = 0xD2800540
// ret → 0xD65F03C0
expectBytes('mov x0, #42; ret',
  'mov x0, #42\nret',
  [0xD2800540, 0xD65F03C0])

// mov x0, x1 → ORR x0, xzr, x1: 0xAA0003E0 | (1 << 16) | 0 = 0xAA0103E0
expectBytes('mov x0, x1 (reg-reg)',
  'mov x0, x1\nret',
  [0xAA0103E0, 0xD65F03C0])

// mov w0, #42 → MOVZ 32-bit: 0x52800000 | (42 << 5) | 0 = 0x52800540
expectBytes('mov w0, #42',
  'mov w0, #42\nret',
  [0x52800540, 0xD65F03C0])

// ══════════════════════════════════════
console.log('\n── Arithmetic ──')

// add x0, x0, #3 → 0x91000000 | (3 << 10) | (0 << 5) | 0 = 0x91000C00
expectBytes('add x0, x0, #3; ret',
  'add x0, x0, #3\nret',
  [0x91000C00, 0xD65F03C0])

// add x0, x1, x2 → 0x8B000000 | (2 << 16) | (1 << 5) | 0 = 0x8B020020
expectBytes('add x0, x1, x2 (reg)',
  'add x0, x1, x2\nret',
  [0x8B020020, 0xD65F03C0])

// sub x0, x0, #1 → 0xD1000000 | (1 << 10) | (0 << 5) | 0 = 0xD1000400
expectBytes('sub x0, x0, #1',
  'sub x0, x0, #1\nret',
  [0xD1000400, 0xD65F03C0])

// sub x0, x1, x2 → 0xCB000000 | (2 << 16) | (1 << 5) | 0 = 0xCB020020
expectBytes('sub x0, x1, x2',
  'sub x0, x1, x2\nret',
  [0xCB020020, 0xD65F03C0])

// mul x0, x0, x1 → MADD: 0x9B007C00 | (1 << 16) | (0 << 5) | 0 = 0x9B017C00
expectBytes('mov x0, #10; mov x1, #7; mul x0, x0, x1; ret',
  'mov x0, #10\nmov x1, #7\nmul x0, x0, x1\nret',
  [
    0xD2800000 | (10 << 5) | 0,   // mov x0, #10
    0xD2800000 | (7 << 5) | 1,    // mov x1, #7
    0x9B017C00,                     // mul x0, x0, x1
    0xD65F03C0                      // ret
  ])

// udiv x0, x1, x2 → 0x9AC00800 | (2 << 16) | (1 << 5) | 0 = 0x9AC20820
expectBytes('udiv x0, x1, x2',
  'udiv x0, x1, x2\nret',
  [0x9AC20820, 0xD65F03C0])

// sdiv x0, x1, x2 → 0x9AC00C00 | (2 << 16) | (1 << 5) | 0 = 0x9AC20C20
expectBytes('sdiv x0, x1, x2',
  'sdiv x0, x1, x2\nret',
  [0x9AC20C20, 0xD65F03C0])

// ══════════════════════════════════════
console.log('\n── Bitwise ──')

// and x0, x1, x2 → 0x8A000000 | (2 << 16) | (1 << 5) | 0 = 0x8A020020
expectBytes('and x0, x1, x2',
  'and x0, x1, x2\nret',
  [0x8A020020, 0xD65F03C0])

// orr x0, x1, x2 → 0xAA000000 | (2 << 16) | (1 << 5) | 0 = 0xAA020020
expectBytes('orr x0, x1, x2',
  'orr x0, x1, x2\nret',
  [0xAA020020, 0xD65F03C0])

// eor x0, x1, x2 → 0xCA000000 | (2 << 16) | (1 << 5) | 0 = 0xCA020020
expectBytes('eor x0, x1, x2',
  'eor x0, x1, x2\nret',
  [0xCA020020, 0xD65F03C0])

// lsl x0, x1, #4 → UBFM x0, x1, #60, #59
// immr = (-4) & 63 = 60, imms = 63 - 4 = 59
// 0xD3400000 | (60 << 16) | (59 << 10) | (1 << 5) | 0
expectBytes('lsl x0, x1, #4',
  'lsl x0, x1, #4\nret',
  [(0xD3400000 | (60 << 16) | (59 << 10) | (1 << 5) | 0) >>> 0, 0xD65F03C0])

// lsr x0, x1, #4 → UBFM x0, x1, #4, #63
// 0xD340FC00 | (4 << 16) | (1 << 5) | 0
expectBytes('lsr x0, x1, #4',
  'lsr x0, x1, #4\nret',
  [(0xD340FC00 | (4 << 16) | (1 << 5) | 0) >>> 0, 0xD65F03C0])

// ══════════════════════════════════════
console.log('\n── Compare ──')

// cmp x0, #10 → SUBS xzr, x0, #10: 0xF100001F | (10 << 10) | (0 << 5) = 0xF100281F
expectBytes('cmp x0, #10',
  'cmp x0, #10\nret',
  [0xF100281F, 0xD65F03C0])

// cmp x0, x1 → SUBS xzr, x0, x1: 0xEB00001F | (1 << 16) | (0 << 5)
expectBytes('cmp x0, x1',
  'cmp x0, x1\nret',
  [(0xEB00001F | (1 << 16) | (0 << 5)) >>> 0, 0xD65F03C0])

// ══════════════════════════════════════
console.log('\n── Memory ──')

// ldr x0, [x1] → 0xF9400000 | (0 << 10) | (1 << 5) | 0 = 0xF9400020
expectBytes('ldr x0, [x1]',
  'ldr x0, [x1]\nret',
  [0xF9400020, 0xD65F03C0])

// ldr x0, [x1, #16] → off/8 = 2, 0xF9400000 | (2 << 10) | (1 << 5) | 0 = 0xF9400820
expectBytes('ldr x0, [x1, #16]',
  'ldr x0, [x1, #16]\nret',
  [0xF9400820, 0xD65F03C0])

// str x0, [x1, #8] → off/8 = 1, 0xF9000000 | (1 << 10) | (1 << 5) | 0 = 0xF9000420
expectBytes('str x0, [x1, #8]',
  'str x0, [x1, #8]\nret',
  [0xF9000420, 0xD65F03C0])

// ldr w0, [x1, #4] → off/4 = 1, 0xB9400000 | (1 << 10) | (1 << 5) | 0 = 0xB9400420
expectBytes('ldr w0, [x1, #4]',
  'ldr w0, [x1, #4]\nret',
  [0xB9400420, 0xD65F03C0])

// ldrb w0, [x1] → 0x39400000 | (1 << 5) | 0 = 0x39400020
expectBytes('ldrb w0, [x1]',
  'ldrb w0, [x1]\nret',
  [0x39400020, 0xD65F03C0])

// ldrh w0, [x1] → 0x79400000 | (1 << 5) | 0 = 0x79400020
expectBytes('ldrh w0, [x1]',
  'ldrh w0, [x1]\nret',
  [0x79400020, 0xD65F03C0])

// ══════════════════════════════════════
console.log('\n── Branch + Labels ──')

// Simple b label (forward jump)
// b skip → 0x14000000 | (2 & 0x3FFFFFF) = 0x14000002  (skip 2 instructions ahead)
// mov x0, #99 (skipped)
// skip: mov x0, #42; ret
expectBytes('b forward',
  'b skip\nmov x0, #99\nskip:\nmov x0, #42\nret',
  [
    0x14000002,               // b skip (+2 words)
    0xD2800000 | (99 << 5),   // mov x0, #99
    0xD2800000 | (42 << 5),   // mov x0, #42
    0xD65F03C0                // ret
  ])

// bl label
expectBytes('bl label',
  'bl target\ntarget:\nret',
  [
    0x94000001,   // bl +1
    0xD65F03C0
  ])

// cmp + b.ne loop (backward branch)
// mov x0, #10
// loop: sub x0, x0, #1  (word index 1)
// cmp x0, #0
// b.ne loop  → offset = 1 - 3 = -2 words
// ret
expectBytes('cmp + b.ne loop (label fixup)',
  'mov x0, #10\nloop:\nsub x0, x0, #1\ncmp x0, #0\nb.ne loop\nret',
  [
    0xD2800000 | (10 << 5),    // mov x0, #10
    0xD1000400,                 // sub x0, x0, #1
    0xF100001F,                 // cmp x0, #0
    // b.ne loop: offset = 1 - 3 = -2, cond=0x1 (ne)
    // 0x54000000 | ((-2 & 0x7FFFF) << 5) | 1
    (0x54000000 | (((-2) & 0x7FFFF) << 5) | 0x1) >>> 0,
    0xD65F03C0
  ])

// b.eq forward
expectBytes('b.eq forward',
  'cmp x0, #0\nb.eq done\nmov x0, #1\ndone:\nret',
  [
    0xF100001F,                 // cmp x0, #0
    (0x54000000 | ((2 & 0x7FFFF) << 5) | 0x0) >>> 0,  // b.eq +2
    0xD2800000 | (1 << 5),     // mov x0, #1
    0xD65F03C0
  ])

// cbz
expectBytes('cbz x0, label',
  'cbz x0, skip\nmov x1, #1\nskip:\nret',
  [
    (0xB4000000 | ((2 & 0x7FFFF) << 5) | 0) >>> 0,  // cbz x0, +2
    0xD2800000 | (1 << 5) | 1,   // mov x1, #1
    0xD65F03C0
  ])

// cbnz
expectBytes('cbnz x0, label',
  'cbnz x0, skip\nmov x1, #1\nskip:\nret',
  [
    (0xB5000000 | ((2 & 0x7FFFF) << 5) | 0) >>> 0,  // cbnz x0, +2
    0xD2800000 | (1 << 5) | 1,   // mov x1, #1
    0xD65F03C0
  ])

// ══════════════════════════════════════
console.log('\n── Guard Rail Scanner ──')

// Block SVC
{
  const buf = assembleARM('svc #0\nret')
  const scan = scanBinaryARM(Array.from(buf))
  assert(!scan.safe, 'svc blocked')
  assert(scan.errors.some(e => e.includes('SVC')), 'svc error message')
}

// Block HLT
{
  const buf = assembleARM('hlt #0\nret')
  const scan = scanBinaryARM(Array.from(buf))
  assert(!scan.safe, 'hlt blocked')
  assert(scan.errors.some(e => e.includes('HLT')), 'hlt error message')
}

// Warn no ret
{
  const buf = assembleARM('mov x0, #42')
  const scan = scanBinaryARM(Array.from(buf))
  assert(scan.safe, 'no ret is warning not error')
  assert(scan.warnings.some(w => w.includes('RET')), 'no ret warning message')
}

// Block oversized
{
  // Create a fake oversized buffer
  const big = new Array(MAX_BINARY_SIZE + 4).fill(0)
  const scan = scanBinaryARM(big)
  assert(!scan.safe, 'oversized binary blocked')
  assert(scan.errors.some(e => e.includes('too large')), 'oversized error message')
}

// Safe binary passes
{
  const { binary, scan } = assembleAndValidateARM('mov x0, #42\nret')
  assert(scan.safe, 'valid binary passes scan')
  assert(scan.warnings.length === 0, 'valid binary no warnings')
}

// assembleAndValidateARM throws on svc
{
  let threw = false
  try {
    assembleAndValidateARM('svc #0\nret')
  } catch (e) {
    threw = true
    assert(e.scan !== undefined, 'assembleAndValidateARM error has scan property')
  }
  assert(threw, 'assembleAndValidateARM throws on svc')
}

// ══════════════════════════════════════
console.log('\n── Misc ──')

// nop
expectBytes('nop',
  'nop\nret',
  [0xD503201F, 0xD65F03C0])

// Multiple conditions
expectBytes('b.lt',
  'cmp x0, #5\nb.lt less\nmov x0, #0\nless:\nret',
  [
    0xF100001F | (5 << 10),
    (0x54000000 | ((2 & 0x7FFFF) << 5) | 0xB) >>> 0,  // lt = 0xB
    0xD2800000,
    0xD65F03C0
  ])

expectBytes('b.ge',
  'cmp x0, #5\nb.ge more\nmov x0, #0\nmore:\nret',
  [
    0xF100001F | (5 << 10),
    (0x54000000 | ((2 & 0x7FFFF) << 5) | 0xA) >>> 0,  // ge = 0xA
    0xD2800000,
    0xD65F03C0
  ])

// Unknown instruction throws
expectThrow('unknown instruction', 'foobar x0, x1')

// Unknown label throws
expectThrow('unknown label', 'b nonexistent')

// ══════════════════════════════════════
console.log(`\n${'═'.repeat(40)}`)
console.log(`Results: ${passed} passed, ${failed} failed`)
if (failed > 0) process.exit(1)
else console.log('All tests passed!')
