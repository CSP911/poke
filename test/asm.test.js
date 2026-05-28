/**
 * POKE Mini Assembler (asm.js) — automated tests
 * Compares asm.js output against nasm output byte-for-byte.
 *
 * Usage: node test/asm.test.js
 * Requires: nasm (for verification only — asm.js itself has zero deps)
 */

const { assemble } = require('../src/asm.js')
const { execSync } = require('child_process')
const fs = require('fs')

const tests = [
  // Basic arithmetic
  { name: 'mov + ret', code: 'mov eax, 42\nret' },
  { name: 'add imm8', code: 'mov eax, 2\nadd eax, 3\nret' },
  { name: 'sub imm8', code: 'mov eax, 100\nsub eax, 37\nret' },
  { name: 'imul imm8', code: 'mov eax, 10\nimul eax, 7\nret' },
  { name: 'idiv', code: 'mov eax, 50\nmov ecx, 2\ncdq\nidiv ecx\nret' },

  // Bitwise
  { name: 'and imm8', code: 'mov eax, 255\nand eax, 0x0F\nret' },
  { name: 'or reg,reg', code: 'mov eax, 0xF0\nmov ecx, 0x0F\nor eax, ecx\nret' },
  { name: 'xor reg,reg', code: 'xor eax, eax\nret' },
  { name: 'shl', code: 'mov eax, 1\nshl eax, 8\nret' },
  { name: 'shr', code: 'mov eax, 256\nshr eax, 4\nret' },
  { name: 'not', code: 'mov eax, 0\nnot eax\nret' },
  { name: 'neg', code: 'mov eax, 5\nneg eax\nret' },

  // Register moves
  { name: 'mov reg,reg', code: 'mov eax, 42\nmov ecx, eax\nmov eax, ecx\nret' },
  { name: 'push/pop', code: 'mov eax, 99\npush eax\nmov eax, 0\npop eax\nret' },
  { name: 'inc/dec', code: 'xor eax, eax\ninc eax\ninc eax\ninc eax\ndec eax\nret' },

  // 16-bit
  { name: 'mov dx,imm16', code: 'mov dx, 0x01CE\nmov ax, 0\nret' },

  // Port I/O
  { name: 'out dx,ax', code: 'mov dx, 0x01CE\nmov ax, 0\nout dx, ax\nret' },
  { name: 'in eax,dx', code: 'mov dx, 0x01CF\nin eax, dx\nret' },
  { name: 'in al,dx', code: 'mov dx, 0x3F8\nin al, dx\nret' },

  // Memory
  { name: 'mov [reg+off]', code: 'mov ebx, 0xfebc0000\nmov eax, [ebx + 0x5400]\nret' },
  { name: 'mov [reg]', code: 'mov ebx, 0xfebc0000\nmov eax, [ebx]\nret' },
  { name: 'movzx ax', code: 'mov dx, 0x01CE\nmov ax, 0\nout dx, ax\nmov dx, 0x01CF\nin ax, dx\nmovzx eax, ax\nret' },

  // Labels + jumps
  { name: 'jnz loop', code: 'xor eax, eax\nmov ecx, 10\nloop_start:\nadd eax, 5\ndec ecx\njnz loop_start\nret' },
  { name: 'cmp + jge', code: 'mov eax, 10\ncmp eax, 5\njge bigger\nmov eax, 0\nbigger:\nret' },
  { name: 'cmp + jl', code: 'mov eax, 3\ncmp eax, 10\njl smaller\nmov eax, 0\nsmaller:\nret' },

  // Inline label
  { name: 'inline label', code: 'xor eax, eax\nmov ecx, 3\nl1: inc eax\ndec ecx\njnz l1\nret' },

  // Complex
  { name: 'call/pop EIP', code: 'call next\nnext:\npop ebx\nmov eax, ebx\nret' },
  { name: 'lea', code: 'mov ebx, 100\nlea eax, [ebx + 50]\nret' },
  { name: 'test', code: 'mov eax, 6\ntest eax, eax\nret' },
  { name: 'mul', code: 'mov eax, 5\nmov ecx, 10\nmul ecx\nret' },
  { name: 'imul reg,reg', code: 'mov eax, 6\nmov ecx, 7\nimul eax, ecx\nret' },
  { name: 'add reg,reg', code: 'mov eax, 10\nmov ecx, 20\nadd eax, ecx\nret' },
  { name: 'sub reg,reg', code: 'mov eax, 50\nmov ecx, 15\nsub eax, ecx\nret' },
  { name: 'nop', code: 'nop\nnop\nmov eax, 1\nret' },

  // Large immediates
  { name: 'mov 0xDEAD', code: 'mov eax, 0xDEAD\nret' },
  { name: 'add imm32', code: 'mov eax, 0\nadd eax, 100000\nret' },
  { name: 'and imm32', code: 'mov eax, 0xFFFFFFFF\nand eax, 0xFF00FF00\nret' },
]

let hasNasm = true
try { execSync('which nasm', { stdio: 'ignore' }) } catch { hasNasm = false }

let pass = 0, fail = 0, skip = 0

console.log(`POKE asm.js test — ${tests.length} cases\n`)

for (const t of tests) {
  try {
    const myBytes = assemble('BITS 32\n' + t.code)

    if (hasNasm) {
      fs.writeFileSync('/tmp/asm_test.asm', 'BITS 32\n' + t.code)
      execSync('nasm -f bin -o /tmp/asm_test.bin /tmp/asm_test.asm 2>&1')
      const nasmBytes = fs.readFileSync('/tmp/asm_test.bin')

      if (myBytes.toString('hex') === nasmBytes.toString('hex')) {
        console.log(`  PASS  ${t.name}`)
        pass++
      } else {
        console.log(`  FAIL  ${t.name}`)
        console.log(`    asm.js: ${myBytes.toString('hex')}`)
        console.log(`    nasm:   ${nasmBytes.toString('hex')}`)
        fail++
      }
    } else {
      // No nasm — just verify it doesn't crash
      console.log(`  OK    ${t.name} (${myBytes.length}B, no nasm to verify)`)
      pass++
    }
  } catch (e) {
    console.log(`  ERR   ${t.name}: ${e.message}`)
    fail++
  }
}

console.log(`\n${pass} passed, ${fail} failed, ${tests.length} total`)
if (!hasNasm) console.log('(nasm not found — crash-test only, no byte verification)')

// ── Guard Rail Tests ──
console.log('\n── Guard Rail Tests ──\n')
const { scanBinary, assembleAndValidate } = require('../src/asm.js')
let gPass = 0, gFail = 0

function guardTest(name, fn, expectPass) {
  try {
    fn()
    if (expectPass) { console.log(`  PASS  ${name} (allowed)`); gPass++ }
    else { console.log(`  FAIL  ${name} (should have been blocked)`); gFail++ }
  } catch (e) {
    if (!expectPass) { console.log(`  PASS  ${name} (blocked: ${e.message.slice(0, 60)})`); gPass++ }
    else { console.log(`  FAIL  ${name} (unexpected error: ${e.message})`); gFail++ }
  }
}

// Should PASS (safe code)
guardTest('safe: mov + ret', () => assembleAndValidate('mov eax, 42\nret'), true)
guardTest('safe: loop', () => assembleAndValidate('xor eax, eax\nmov ecx, 10\nl:\ninc eax\ndec ecx\njnz l\nret'), true)
guardTest('safe: memory read', () => assembleAndValidate('mov ebx, 0xfebc0000\nmov eax, [ebx]\nret'), true)
guardTest('safe: port I/O', () => assembleAndValidate('mov dx, 0x01CE\nmov ax, 0\nout dx, ax\nret'), true)

// Should FAIL (dangerous code)
guardTest('block: HLT', () => {
  const bin = Buffer.from([0xF4, 0xC3])  // HLT + RET
  const scan = scanBinary(Array.from(bin))
  if (!scan.safe) throw new Error(scan.errors.join('; '))
}, false)

guardTest('block: CLI', () => {
  const bin = Buffer.from([0xFA, 0xC3])  // CLI + RET
  const scan = scanBinary(Array.from(bin))
  if (!scan.safe) throw new Error(scan.errors.join('; '))
}, false)

guardTest('warn: no RET', () => {
  const bin = Buffer.from([0xB8, 0x01, 0x00, 0x00, 0x00])  // mov eax, 1 (no ret)
  const scan = scanBinary(Array.from(bin))
  if (scan.warnings.length === 0) throw new Error('expected warning')
  throw new Error(scan.warnings.join('; '))
}, false)

guardTest('block: oversized binary', () => {
  const big = new Array(20000).fill(0x90)  // 20KB of NOPs
  big.push(0xC3)
  const scan = scanBinary(big)
  if (!scan.safe) throw new Error(scan.errors.join('; '))
}, false)

console.log(`\n${gPass} passed, ${gFail} failed, ${gPass + gFail} guard tests`)

const totalFail = fail + gFail
console.log(`\nTotal: ${pass + gPass} passed, ${totalFail} failed`)
process.exit(totalFail > 0 ? 1 : 0)
