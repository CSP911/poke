/**
 * POKE Mini Assembler — x86-32 어셈블러 (JS 내장, 외부 의존성 0)
 *
 * LLM이 생성한 어셈블리 텍스트를 바이트로 변환합니다.
 * nasm 불필요. hub.js에서 require해서 사용.
 */

function assemble(source) {
  let lines = source.split('\n')
    .map(l => l.replace(/;.*$/, '').trim())  // 주석 제거
    .filter(l => l && !l.startsWith('BITS'))  // 빈줄, BITS 지시자 제거

  // "label: instruction" → split into two lines
  const expanded = []
  for (const l of lines) {
    const m = l.match(/^(\w+):\s+(.+)$/)
    if (m) { expanded.push(m[1] + ':'); expanded.push(m[2]) }
    else expanded.push(l)
  }
  lines = expanded

  const bytes = []
  const labels = {}     // label → byte offset
  const fixups = []     // { pos, label, type } — 나중에 패치할 위치

  // 1st pass: 라벨 수집 + 인코딩
  for (const raw of lines) {
    const line = raw.toLowerCase().replace(/,/g, ' ').replace(/\s+/g, ' ').trim()

    // ── 라벨 ──
    if (line.endsWith(':')) {
      labels[line.slice(0, -1)] = bytes.length
      continue
    }

    const parts = line.split(' ')
    const op = parts[0]

    // ── RET ──
    if (op === 'ret') { bytes.push(0xC3); continue }

    // ── NOP ──
    if (op === 'nop') { bytes.push(0x90); continue }

    // ── CDQ ──
    if (op === 'cdq') { bytes.push(0x99); continue }

    // ── PUSH reg32 ──
    if (op === 'push') {
      const r = reg32(parts[1])
      if (r !== null) { bytes.push(0x50 + r); continue }
    }

    // ── POP reg32 ──
    if (op === 'pop') {
      const r = reg32(parts[1])
      if (r !== null) { bytes.push(0x58 + r); continue }
    }

    // ── MOV reg16, imm16 (prefix 0x66) ──
    if (op === 'mov' && parts.length >= 3) {
      const dst16 = reg16(parts[1])
      if (dst16 !== null) {
        const imm = parseImm(parts[2])
        if (imm !== null) {
          bytes.push(0x66)
          bytes.push(0xB8 + dst16)
          bytes.push(imm & 0xFF, (imm >> 8) & 0xFF)
          continue
        }
      }

      const dst = reg32(parts[1])
      const src = reg32(parts[2])

      // mov reg, imm
      if (dst !== null && src === null) {
        const imm = parseImm(parts[2])
        if (imm !== null) {
          bytes.push(0xB8 + dst)
          pushLE32(bytes, imm)
          continue
        }
      }

      // mov reg, reg
      if (dst !== null && src !== null) {
        bytes.push(0x89)
        bytes.push(0xC0 | (src << 3) | dst)
        continue
      }

      // mov reg, [reg + offset] — MMIO 읽기
      const memRead = matchMemRead(parts.slice(2).join(' '))
      if (dst !== null && memRead) {
        bytes.push(0x8B)
        encodeMemOp(bytes, dst, memRead)
        continue
      }

      // mov [reg + offset], reg — MMIO 쓰기
      const memWrite = matchMemRead(parts[1])
      if (memWrite && src !== null) {
        bytes.push(0x89)
        encodeMemOp(bytes, src, memWrite)
        continue
      }
    }

    // ── MOVZX reg32, WORD [reg+off] ──
    if (op === 'movzx' && parts.length >= 3) {
      const dst = reg32(parts[1])
      if (dst !== null) {
        const rest = parts.slice(2).join(' ')
        if (rest.includes('word')) {
          const mem = matchMemRead(rest.replace('word', '').trim())
          if (mem) {
            bytes.push(0x0F, 0xB7)
            encodeMemOp(bytes, dst, mem)
            continue
          }
        }
        if (rest.includes('byte')) {
          const mem = matchMemRead(rest.replace('byte', '').trim())
          if (mem) {
            bytes.push(0x0F, 0xB6)
            encodeMemOp(bytes, dst, mem)
            continue
          }
        }
        // movzx reg, ax/bx/cx/dx (16-bit)
        const src16 = reg16(parts[2])
        if (src16 !== null) {
          bytes.push(0x0F, 0xB7)
          bytes.push(0xC0 | (dst << 3) | src16)
          continue
        }
      }
    }

    // ── XOR reg, reg ──
    if (op === 'xor') {
      const dst = reg32(parts[1])
      const src = reg32(parts[2])
      if (dst !== null && src !== null) {
        bytes.push(0x31)
        bytes.push(0xC0 | (src << 3) | dst)
        continue
      }
    }

    // ── ADD reg, imm8 ──
    if (op === 'add') {
      const dst = reg32(parts[1])
      const src = reg32(parts[2])
      if (dst !== null && src !== null) {
        bytes.push(0x01)
        bytes.push(0xC0 | (src << 3) | dst)
        continue
      }
      if (dst !== null) {
        const imm = parseImm(parts[2])
        if (imm !== null) {
          if (dst === 0 && (imm > 127 || imm < -128)) {
            // add eax, imm32
            bytes.push(0x05)
            pushLE32(bytes, imm)
          } else if (imm >= -128 && imm <= 127) {
            bytes.push(0x83)
            bytes.push(0xC0 | dst)
            bytes.push(imm & 0xFF)
          } else {
            bytes.push(0x81)
            bytes.push(0xC0 | dst)
            pushLE32(bytes, imm)
          }
          continue
        }
      }
    }

    // ── SUB reg, imm8 ──
    if (op === 'sub') {
      const dst = reg32(parts[1])
      const src = reg32(parts[2])
      if (dst !== null && src !== null) {
        bytes.push(0x29)
        bytes.push(0xC0 | (src << 3) | dst)
        continue
      }
      if (dst !== null) {
        const imm = parseImm(parts[2])
        if (imm !== null) {
          if (imm >= -128 && imm <= 127) {
            bytes.push(0x83)
            bytes.push(0xE8 | dst)
            bytes.push(imm & 0xFF)
          } else {
            bytes.push(0x81)
            bytes.push(0xE8 | dst)
            pushLE32(bytes, imm)
          }
          continue
        }
      }
    }

    // ── IMUL ──
    if (op === 'imul') {
      const dst = reg32(parts[1])
      const src = reg32(parts[2])
      const imm = parts[3] ? parseImm(parts[3]) : (src === null ? parseImm(parts[2]) : null)

      // imul reg, reg, imm32
      if (dst !== null && src !== null && imm !== null) {
        if (imm >= -128 && imm <= 127) {
          bytes.push(0x6B)
          bytes.push(0xC0 | (dst << 3) | src)
          bytes.push(imm & 0xFF)
        } else {
          bytes.push(0x69)
          bytes.push(0xC0 | (dst << 3) | src)
          pushLE32(bytes, imm)
        }
        continue
      }

      // imul reg, imm (= imul reg, reg, imm)
      if (dst !== null && src === null && imm !== null) {
        if (imm >= -128 && imm <= 127) {
          bytes.push(0x6B)
          bytes.push(0xC0 | (dst << 3) | dst)
          bytes.push(imm & 0xFF)
        } else {
          bytes.push(0x69)
          bytes.push(0xC0 | (dst << 3) | dst)
          pushLE32(bytes, imm)
        }
        continue
      }

      // imul reg, reg
      if (dst !== null && src !== null) {
        bytes.push(0x0F, 0xAF)
        bytes.push(0xC0 | (dst << 3) | src)
        continue
      }
    }

    // ── MUL reg (unsigned: edx:eax = eax * reg) ──
    if (op === 'mul') {
      const src = reg32(parts[1])
      if (src !== null) {
        bytes.push(0xF7)
        bytes.push(0xE0 | src)
        continue
      }
    }

    // ── IDIV reg ──
    if (op === 'idiv') {
      const src = reg32(parts[1])
      if (src !== null) {
        bytes.push(0xF7)
        bytes.push(0xF8 | src)
        continue
      }
    }

    // ── DIV reg ──
    if (op === 'div') {
      const src = reg32(parts[1])
      if (src !== null) {
        bytes.push(0xF7)
        bytes.push(0xF0 | src)
        continue
      }
    }

    // ── AND reg, imm / AND reg, reg ──
    if (op === 'and') {
      const dst = reg32(parts[1])
      const src = reg32(parts[2])
      if (dst !== null && src !== null) {
        bytes.push(0x21)
        bytes.push(0xC0 | (src << 3) | dst)
        continue
      }
      if (dst !== null) {
        const imm = parseImm(parts[2])
        if (imm !== null) {
          if (imm >= -128 && imm <= 127) {
            bytes.push(0x83); bytes.push(0xE0 | dst); bytes.push(imm & 0xFF)
          } else if (dst === 0) {
            bytes.push(0x25); pushLE32(bytes, imm)
          } else {
            bytes.push(0x81); bytes.push(0xE0 | dst); pushLE32(bytes, imm)
          }
          continue
        }
      }
    }

    // ── OR reg, imm / OR reg, reg ──
    if (op === 'or') {
      const dst = reg32(parts[1])
      const src = reg32(parts[2])
      if (dst !== null && src !== null) {
        bytes.push(0x09)
        bytes.push(0xC0 | (src << 3) | dst)
        continue
      }
      if (dst !== null) {
        const imm = parseImm(parts[2])
        if (imm !== null) {
          if (dst === 0) { bytes.push(0x0D); pushLE32(bytes, imm); }
          else { bytes.push(0x81); bytes.push(0xC8 | dst); pushLE32(bytes, imm); }
          continue
        }
      }
    }

    // ── SHL reg, imm8 ──
    if (op === 'shl') {
      const dst = reg32(parts[1])
      const imm = parseImm(parts[2])
      if (dst !== null && imm !== null) {
        bytes.push(0xC1)
        bytes.push(0xE0 | dst)
        bytes.push(imm & 0xFF)
        continue
      }
    }

    // ── SHR reg, imm8 ──
    if (op === 'shr') {
      const dst = reg32(parts[1])
      const imm = parseImm(parts[2])
      if (dst !== null && imm !== null) {
        bytes.push(0xC1)
        bytes.push(0xE8 | dst)
        bytes.push(imm & 0xFF)
        continue
      }
    }

    // ── CMP reg, imm ──
    if (op === 'cmp') {
      const dst = reg32(parts[1])
      const src = reg32(parts[2])
      if (dst !== null && src !== null) {
        bytes.push(0x39)
        bytes.push(0xC0 | (src << 3) | dst)
        continue
      }
      if (dst !== null) {
        const imm = parseImm(parts[2])
        if (imm !== null) {
          if (imm >= -128 && imm <= 127) {
            bytes.push(0x83)
            bytes.push(0xF8 | dst)
            bytes.push(imm & 0xFF)
          } else {
            bytes.push(0x81)
            bytes.push(0xF8 | dst)
            pushLE32(bytes, imm)
          }
          continue
        }
      }
    }

    // ── TEST reg, reg ──
    if (op === 'test') {
      const dst = reg32(parts[1])
      const src = reg32(parts[2])
      if (dst !== null && src !== null) {
        bytes.push(0x85)
        bytes.push(0xC0 | (src << 3) | dst)
        continue
      }
    }

    // ── NOT reg ──
    if (op === 'not') {
      const dst = reg32(parts[1])
      if (dst !== null) { bytes.push(0xF7); bytes.push(0xD0 | dst); continue }
    }

    // ── NEG reg ──
    if (op === 'neg') {
      const dst = reg32(parts[1])
      if (dst !== null) { bytes.push(0xF7); bytes.push(0xD8 | dst); continue }
    }

    // ── INC reg ──
    if (op === 'inc') {
      const dst = reg32(parts[1])
      if (dst !== null) { bytes.push(0x40 + dst); continue }
    }

    // ── DEC reg ──
    if (op === 'dec') {
      const dst = reg32(parts[1])
      if (dst !== null) { bytes.push(0x48 + dst); continue }
    }

    // ── LEA reg, [reg + offset] ──
    if (op === 'lea') {
      const dst = reg32(parts[1])
      const mem = matchMemRead(parts.slice(2).join(' '))
      if (dst !== null && mem) {
        bytes.push(0x8D)
        encodeMemOp(bytes, dst, mem)
        continue
      }
    }

    // ── Jcc (조건 분기) — 라벨은 fixup으로 ──
    const jccOps = {
      'jmp': 0xEB, 'je': 0x74, 'jz': 0x74, 'jne': 0x75, 'jnz': 0x75,
      'jl': 0x7C, 'jge': 0x7D, 'jle': 0x7E, 'jg': 0x7F,
      'jb': 0x72, 'jae': 0x73, 'jbe': 0x76, 'ja': 0x77,
    }
    if (jccOps[op]) {
      const target = parts[1]
      bytes.push(jccOps[op])
      fixups.push({ pos: bytes.length, label: target, type: 'rel8' })
      bytes.push(0x00)  // placeholder
      continue
    }

    // ── CALL label ──
    if (op === 'call') {
      bytes.push(0xE8)
      fixups.push({ pos: bytes.length, label: parts[1], type: 'rel32' })
      bytes.push(0, 0, 0, 0)
      continue
    }

    // ── IN/OUT (port I/O) ──
    if (op === 'in') {
      const dst = parts[1]
      const src = parts[2]
      if (dst === 'al' && src === 'dx') { bytes.push(0xEC); continue }
      if (dst === 'ax' && src === 'dx') { bytes.push(0x66, 0xED); continue }
      if (dst === 'eax' && src === 'dx') { bytes.push(0xED); continue }
    }
    if (op === 'out') {
      const dst = parts[1]
      const src = parts[2]
      if (dst === 'dx' && src === 'al') { bytes.push(0xEE); continue }
      if (dst === 'dx' && src === 'ax') { bytes.push(0x66, 0xEF); continue }
      if (dst === 'dx' && src === 'eax') { bytes.push(0xEF); continue }
    }

    // ── 알 수 없는 명령 ──
    throw new Error(`Unknown instruction: "${raw}"`)
  }

  // 2nd pass: fixup (라벨 참조 해결)
  for (const f of fixups) {
    const target = labels[f.label]
    if (target === undefined) throw new Error(`Unknown label: ${f.label}`)
    if (f.type === 'rel8') {
      const offset = target - (f.pos + 1)
      if (offset < -128 || offset > 127) throw new Error(`Jump too far: ${f.label}`)
      bytes[f.pos] = offset & 0xFF
    } else if (f.type === 'rel32') {
      const offset = target - (f.pos + 4)
      bytes[f.pos] = offset & 0xFF
      bytes[f.pos + 1] = (offset >> 8) & 0xFF
      bytes[f.pos + 2] = (offset >> 16) & 0xFF
      bytes[f.pos + 3] = (offset >> 24) & 0xFF
    }
  }

  return Buffer.from(bytes)
}

// ── 헬퍼 ──
const REGS32 = { eax: 0, ecx: 1, edx: 2, ebx: 3, esp: 4, ebp: 5, esi: 6, edi: 7 }
const REGS16 = { ax: 0, cx: 1, dx: 2, bx: 3, sp: 4, bp: 5, si: 6, di: 7 }

function reg32(s) { return s ? REGS32[s.toLowerCase()] ?? null : null }
function reg16(s) { return s ? REGS16[s.toLowerCase()] ?? null : null }

function parseImm(s) {
  if (!s) return null
  s = s.trim()
  if (s.startsWith('0x') || s.startsWith('0X')) return parseInt(s, 16)
  if (s.startsWith('-')) return parseInt(s, 10)
  const n = parseInt(s, 10)
  return isNaN(n) ? null : n
}

function pushLE32(arr, val) {
  val = val >>> 0  // unsigned 32
  arr.push(val & 0xFF, (val >> 8) & 0xFF, (val >> 16) & 0xFF, (val >> 24) & 0xFF)
}

function hex8(v) { return (v & 0xFF).toString(16).padStart(2, '0') }

// [reg], [reg + offset], [imm32]
function matchMemRead(s) {
  s = s.replace(/[\[\]]/g, '').trim()
  // reg + offset
  let m = s.match(/^(\w+)\s*\+\s*(.+)$/)
  if (m) {
    const base = reg32(m[1])
    const off = parseImm(m[2].trim())
    if (base !== null && off !== null) return { base, offset: off }
  }
  // reg - offset
  m = s.match(/^(\w+)\s*-\s*(.+)$/)
  if (m) {
    const base = reg32(m[1])
    const off = parseImm(m[2].trim())
    if (base !== null && off !== null) return { base, offset: -off }
  }
  // [reg]
  const base = reg32(s)
  if (base !== null) return { base, offset: 0 }
  // [imm32]
  const imm = parseImm(s)
  if (imm !== null) return { base: null, offset: imm }
  return null
}

function encodeMemOp(bytes, reg, mem) {
  if (mem.base === null) {
    // [disp32]
    bytes.push(0x05 | (reg << 3))
    pushLE32(bytes, mem.offset)
  } else if (mem.offset === 0 && mem.base !== 5) {
    // [reg] (no displacement, except ebp always needs disp8)
    bytes.push((reg << 3) | mem.base)
    if (mem.base === 4) bytes.push(0x24) // SIB for esp
  } else if (mem.offset >= -128 && mem.offset <= 127) {
    // [reg + disp8]
    bytes.push(0x40 | (reg << 3) | mem.base)
    if (mem.base === 4) bytes.push(0x24)
    bytes.push(mem.offset & 0xFF)
  } else {
    // [reg + disp32]
    bytes.push(0x80 | (reg << 3) | mem.base)
    if (mem.base === 4) bytes.push(0x24)
    pushLE32(bytes, mem.offset)
  }
}

module.exports = { assemble }
