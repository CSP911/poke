/**
 * POKE Mini Assembler — ARM64 (AArch64) assembler (JS built-in, zero deps)
 *
 * LLM이 생성한 ARM64 어셈블리 텍스트를 바이트로 변환합니다.
 * 외부 도구 불필요. hub.js에서 require해서 사용.
 *
 * All ARM64 instructions are exactly 4 bytes, little-endian.
 */

function assembleARM(source) {
  let lines = source.split('\n')
    .map(l => l.replace(/\/\/.*$/, '').replace(/;.*$/, '').trim())  // 주석 제거
    .filter(l => l.length > 0)

  // "label: instruction" → split into two lines
  const expanded = []
  for (const l of lines) {
    const m = l.match(/^(\w+):\s+(.+)$/)
    if (m) { expanded.push(m[1] + ':'); expanded.push(m[2]) }
    else expanded.push(l)
  }
  lines = expanded

  const buf = []       // raw u32 words (each = 1 instruction)
  const labels = {}    // label → instruction index (word index)
  const fixups = []    // { pos (word index), label, type }

  for (const raw of lines) {
    const line = raw.replace(/,/g, ' ').replace(/\s+/g, ' ').trim()

    // ── Label ──
    if (line.endsWith(':')) {
      labels[line.slice(0, -1)] = buf.length
      continue
    }

    const parts = line.split(' ')
    const op = parts[0].toLowerCase()

    // ── RET ──
    if (op === 'ret') { buf.push(0xD65F03C0); continue }

    // ── NOP ──
    if (op === 'nop') { buf.push(0xD503201F); continue }

    // ── MOV ──
    if (op === 'mov') {
      const rd = parseReg(parts[1])
      const rm = parseReg(parts[2])

      if (rd !== null && rm !== null) {
        // mov xN, xM → ORR xd, xzr, xm
        if (rd.w || rm.w) {
          // 32-bit: ORR wd, wzr, wm → 0x2A0003E0
          buf.push((0x2A0003E0 | (rm.n << 16) | rd.n) >>> 0)
        } else {
          buf.push((0xAA0003E0 | (rm.n << 16) | rd.n) >>> 0)
        }
        continue
      }

      if (rd !== null) {
        const imm = parseImm(parts[2])
        if (imm !== null) {
          if (rd.w) {
            // MOVZ 32-bit: 0x52800000
            buf.push((0x52800000 | ((imm & 0xFFFF) << 5) | rd.n) >>> 0)
          } else {
            // MOVZ 64-bit: 0xD2800000
            buf.push((0xD2800000 | ((imm & 0xFFFF) << 5) | rd.n) >>> 0)
          }
          continue
        }
      }
      throw new Error(`Bad mov: "${raw}"`)
    }

    // ── ADD ──
    if (op === 'add') {
      const rd = parseReg(parts[1])
      const rn = parseReg(parts[2])
      const rm = parseReg(parts[3])
      if (rd && rn && rm) {
        const sf = (rd.w) ? 0x0B000000 : 0x8B000000
        buf.push((sf | (rm.n << 16) | (rn.n << 5) | rd.n) >>> 0)
        continue
      }
      if (rd && rn) {
        const imm = parseImm(parts[3])
        if (imm !== null) {
          const sf = (rd.w) ? 0x11000000 : 0x91000000
          buf.push((sf | ((imm & 0xFFF) << 10) | (rn.n << 5) | rd.n) >>> 0)
          continue
        }
      }
      throw new Error(`Bad add: "${raw}"`)
    }

    // ── SUB ──
    if (op === 'sub') {
      const rd = parseReg(parts[1])
      const rn = parseReg(parts[2])
      const rm = parseReg(parts[3])
      if (rd && rn && rm) {
        const sf = (rd.w) ? 0x4B000000 : 0xCB000000
        buf.push((sf | (rm.n << 16) | (rn.n << 5) | rd.n) >>> 0)
        continue
      }
      if (rd && rn) {
        const imm = parseImm(parts[3])
        if (imm !== null) {
          const sf = (rd.w) ? 0x51000000 : 0xD1000000
          buf.push((sf | ((imm & 0xFFF) << 10) | (rn.n << 5) | rd.n) >>> 0)
          continue
        }
      }
      throw new Error(`Bad sub: "${raw}"`)
    }

    // ── MADD xd, xn, xm, xa ──
    if (op === 'madd') {
      const rd = parseReg(parts[1])
      const rn = parseReg(parts[2])
      const rm = parseReg(parts[3])
      const ra = parseReg(parts[4])
      if (rd && rn && rm && ra) {
        const sf = (rd.w) ? 0x1B000000 : 0x9B000000
        buf.push((sf | (rm.n << 16) | (ra.n << 10) | (rn.n << 5) | rd.n) >>> 0)
        continue
      }
      throw new Error(`Bad madd: "${raw}"`)
    }

    // ── MSUB xd, xn, xm, xa ──
    if (op === 'msub') {
      const rd = parseReg(parts[1])
      const rn = parseReg(parts[2])
      const rm = parseReg(parts[3])
      const ra = parseReg(parts[4])
      if (rd && rn && rm && ra) {
        const sf = (rd.w) ? 0x1B008000 : 0x9B008000
        buf.push((sf | (rm.n << 16) | (ra.n << 10) | (rn.n << 5) | rd.n) >>> 0)
        continue
      }
      throw new Error(`Bad msub: "${raw}"`)
    }

    // ── MUL ── (MADD xd, xn, xm, xzr)
    if (op === 'mul') {
      const rd = parseReg(parts[1])
      const rn = parseReg(parts[2])
      const rm = parseReg(parts[3])
      if (rd && rn && rm) {
        const sf = (rd.w) ? 0x1B007C00 : 0x9B007C00
        buf.push((sf | (rm.n << 16) | (rn.n << 5) | rd.n) >>> 0)
        continue
      }
      throw new Error(`Bad mul: "${raw}"`)
    }

    // ── UDIV ──
    if (op === 'udiv') {
      const rd = parseReg(parts[1])
      const rn = parseReg(parts[2])
      const rm = parseReg(parts[3])
      if (rd && rn && rm) {
        const sf = (rd.w) ? 0x1AC00800 : 0x9AC00800
        buf.push((sf | (rm.n << 16) | (rn.n << 5) | rd.n) >>> 0)
        continue
      }
      throw new Error(`Bad udiv: "${raw}"`)
    }

    // ── SDIV ──
    if (op === 'sdiv') {
      const rd = parseReg(parts[1])
      const rn = parseReg(parts[2])
      const rm = parseReg(parts[3])
      if (rd && rn && rm) {
        const sf = (rd.w) ? 0x1AC00C00 : 0x9AC00C00
        buf.push((sf | (rm.n << 16) | (rn.n << 5) | rd.n) >>> 0)
        continue
      }
      throw new Error(`Bad sdiv: "${raw}"`)
    }

    // ── AND ──
    if (op === 'and') {
      const rd = parseReg(parts[1])
      const rn = parseReg(parts[2])
      const rm = parseReg(parts[3])
      if (rd && rn && rm) {
        const sf = (rd.w) ? 0x0A000000 : 0x8A000000
        buf.push((sf | (rm.n << 16) | (rn.n << 5) | rd.n) >>> 0)
        continue
      }
      throw new Error(`Bad and: "${raw}"`)
    }

    // ── ORR ──
    if (op === 'orr') {
      const rd = parseReg(parts[1])
      const rn = parseReg(parts[2])
      const rm = parseReg(parts[3])
      if (rd && rn && rm) {
        const sf = (rd.w) ? 0x2A000000 : 0xAA000000
        buf.push((sf | (rm.n << 16) | (rn.n << 5) | rd.n) >>> 0)
        continue
      }
      throw new Error(`Bad orr: "${raw}"`)
    }

    // ── EOR ──
    if (op === 'eor') {
      const rd = parseReg(parts[1])
      const rn = parseReg(parts[2])
      const rm = parseReg(parts[3])
      if (rd && rn && rm) {
        const sf = (rd.w) ? 0x4A000000 : 0xCA000000
        buf.push((sf | (rm.n << 16) | (rn.n << 5) | rd.n) >>> 0)
        continue
      }
      throw new Error(`Bad eor: "${raw}"`)
    }

    // ── LSL xN, xM, #imm ── (UBFM alias)
    if (op === 'lsl') {
      const rd = parseReg(parts[1])
      const rn = parseReg(parts[2])
      const imm = parseImm(parts[3])
      if (rd && rn && imm !== null) {
        if (rd.w) {
          // UBFM wd, wn, #(-shift MOD 32), #(31 - shift)
          const immr = (-imm) & 31
          const imms = 31 - imm
          buf.push((0x53000000 | (immr << 16) | (imms << 10) | (rn.n << 5) | rd.n) >>> 0)
        } else {
          // UBFM xd, xn, #(-shift MOD 64), #(63 - shift)
          const immr = (-imm) & 63
          const imms = 63 - imm
          buf.push((0xD3400000 | (immr << 16) | (imms << 10) | (rn.n << 5) | rd.n) >>> 0)
        }
        continue
      }
      throw new Error(`Bad lsl: "${raw}"`)
    }

    // ── LSR xN, xM, #imm ── (UBFM alias)
    if (op === 'lsr') {
      const rd = parseReg(parts[1])
      const rn = parseReg(parts[2])
      const imm = parseImm(parts[3])
      if (rd && rn && imm !== null) {
        if (rd.w) {
          // UBFM wd, wn, #shift, #31
          buf.push((0x53000000 | (imm << 16) | (31 << 10) | (rn.n << 5) | rd.n) >>> 0)
        } else {
          // UBFM xd, xn, #shift, #63
          buf.push((0xD340FC00 | (imm << 16) | (rn.n << 5) | rd.n) >>> 0)
        }
        continue
      }
      throw new Error(`Bad lsr: "${raw}"`)
    }

    // ── CMP ──
    if (op === 'cmp') {
      const rn = parseReg(parts[1])
      const rm = parseReg(parts[2])
      if (rn && rm) {
        // SUBS xzr, xn, xm
        const sf = (rn.w) ? 0x6B00001F : 0xEB00001F
        buf.push((sf | (rm.n << 16) | (rn.n << 5)) >>> 0)
        continue
      }
      if (rn) {
        const imm = parseImm(parts[2])
        if (imm !== null) {
          // SUBS xzr, xn, #imm12
          const sf = (rn.w) ? 0x7100001F : 0xF100001F
          buf.push((sf | ((imm & 0xFFF) << 10) | (rn.n << 5)) >>> 0)
          continue
        }
      }
      throw new Error(`Bad cmp: "${raw}"`)
    }

    // ── LDR / STR ──
    if (op === 'ldr' || op === 'str') {
      const rt = parseReg(parts[1])
      const mem = parseMemArm(parts.slice(2).join(' '))
      if (rt && mem) {
        if (op === 'ldr') {
          if (rt.w) {
            // LDR wN, [xM, #off] → 0xB9400000, off/4
            const scaled = mem.offset / 4
            buf.push((0xB9400000 | ((scaled & 0xFFF) << 10) | (mem.base << 5) | rt.n) >>> 0)
          } else {
            // LDR xN, [xM, #off] → 0xF9400000, off/8
            const scaled = mem.offset / 8
            buf.push((0xF9400000 | ((scaled & 0xFFF) << 10) | (mem.base << 5) | rt.n) >>> 0)
          }
        } else {
          // str
          if (rt.w) {
            const scaled = mem.offset / 4
            buf.push((0xB9000000 | ((scaled & 0xFFF) << 10) | (mem.base << 5) | rt.n) >>> 0)
          } else {
            const scaled = mem.offset / 8
            buf.push((0xF9000000 | ((scaled & 0xFFF) << 10) | (mem.base << 5) | rt.n) >>> 0)
          }
        }
        continue
      }
      throw new Error(`Bad ${op}: "${raw}"`)
    }

    // ── LDRB wN, [xM] ──
    if (op === 'ldrb') {
      const rt = parseReg(parts[1])
      const mem = parseMemArm(parts.slice(2).join(' '))
      if (rt && mem) {
        buf.push((0x39400000 | ((mem.offset & 0xFFF) << 10) | (mem.base << 5) | rt.n) >>> 0)
        continue
      }
      throw new Error(`Bad ldrb: "${raw}"`)
    }

    // ── LDRH wN, [xM] ──
    if (op === 'ldrh') {
      const rt = parseReg(parts[1])
      const mem = parseMemArm(parts.slice(2).join(' '))
      if (rt && mem) {
        const scaled = mem.offset / 2
        buf.push((0x79400000 | ((scaled & 0xFFF) << 10) | (mem.base << 5) | rt.n) >>> 0)
        continue
      }
      throw new Error(`Bad ldrh: "${raw}"`)
    }

    // ── B label ──
    if (op === 'b') {
      fixups.push({ pos: buf.length, label: parts[1], type: 'b' })
      buf.push(0x14000000)  // placeholder
      continue
    }

    // ── BL label ──
    if (op === 'bl') {
      fixups.push({ pos: buf.length, label: parts[1], type: 'bl' })
      buf.push(0x94000000)  // placeholder
      continue
    }

    // ── B.cond label ──
    const condMatch = op.match(/^b\.(eq|ne|lt|ge|le|gt|hi|lo|cs|cc|mi|pl|vs|vc|al|hs|ls)$/)
    if (condMatch) {
      const cond = COND_CODES[condMatch[1]]
      if (cond === undefined) throw new Error(`Unknown condition: ${condMatch[1]}`)
      fixups.push({ pos: buf.length, label: parts[1], type: 'bcond', cond })
      buf.push(0x54000000 | cond)  // placeholder
      continue
    }

    // ── CBZ ──
    if (op === 'cbz') {
      const rn = parseReg(parts[1])
      if (rn) {
        const sf = rn.w ? 0x34000000 : 0xB4000000
        fixups.push({ pos: buf.length, label: parts[2], type: 'cb', rn: rn.n })
        buf.push(sf | rn.n)
        continue
      }
      throw new Error(`Bad cbz: "${raw}"`)
    }

    // ── CBNZ ──
    if (op === 'cbnz') {
      const rn = parseReg(parts[1])
      if (rn) {
        const sf = rn.w ? 0x35000000 : 0xB5000000
        fixups.push({ pos: buf.length, label: parts[2], type: 'cb', rn: rn.n })
        buf.push(sf | rn.n)
        continue
      }
      throw new Error(`Bad cbnz: "${raw}"`)
    }

    // ── SVC (should be blocked by scanner, but we still encode it) ──
    if (op === 'svc') {
      const imm = parseImm(parts[1]) || 0
      buf.push((0xD4000001 | ((imm & 0xFFFF) << 5)) >>> 0)
      continue
    }

    // ── HLT ──
    if (op === 'hlt') {
      const imm = parseImm(parts[1]) || 0
      buf.push((0xD4400000 | ((imm & 0xFFFF) << 5)) >>> 0)
      continue
    }

    throw new Error(`Unknown instruction: "${raw}"`)
  }

  // 2nd pass: fixups
  for (const f of fixups) {
    const target = labels[f.label]
    if (target === undefined) throw new Error(`Unknown label: ${f.label}`)
    const offset = target - f.pos  // in words (each instruction = 1 word)

    if (f.type === 'b') {
      buf[f.pos] = (0x14000000 | (offset & 0x3FFFFFF)) >>> 0
    } else if (f.type === 'bl') {
      buf[f.pos] = (0x94000000 | (offset & 0x3FFFFFF)) >>> 0
    } else if (f.type === 'bcond') {
      buf[f.pos] = (0x54000000 | (((offset) & 0x7FFFF) << 5) | f.cond) >>> 0
    } else if (f.type === 'cb') {
      // Preserve existing opcode bits (top byte has sf + op), just patch imm19
      const base = buf[f.pos] & ~(0x7FFFF << 5)  // clear imm19 field
      buf[f.pos] = (base | (((offset) & 0x7FFFF) << 5)) >>> 0
    }
  }

  // Convert u32 words to little-endian byte buffer
  const bytes = Buffer.alloc(buf.length * 4)
  for (let i = 0; i < buf.length; i++) {
    bytes.writeUInt32LE(buf[i] >>> 0, i * 4)
  }
  return bytes
}

// ══════════════════════════════════════
// ── Helpers
// ══════════════════════════════════════

const COND_CODES = {
  eq: 0x0, ne: 0x1, cs: 0x2, hs: 0x2, cc: 0x3, lo: 0x3,
  mi: 0x4, pl: 0x5, vs: 0x6, vc: 0x7,
  hi: 0x8, ls: 0x9, ge: 0xA, lt: 0xB, gt: 0xC, le: 0xD, al: 0xE,
}

/**
 * Parse an ARM64 register name.
 * Returns { n: register number 0-31, w: true if 32-bit } or null.
 */
function parseReg(s) {
  if (!s) return null
  s = s.trim().toLowerCase()
  if (s === 'xzr' || s === 'wzr') return { n: 31, w: s[0] === 'w' }
  if (s === 'sp') return { n: 31, w: false }
  if (s === 'wsp') return { n: 31, w: true }
  const m = s.match(/^([xw])(\d+)$/)
  if (!m) return null
  const n = parseInt(m[2], 10)
  if (n < 0 || n > 30) return null
  return { n, w: m[1] === 'w' }
}

function parseImm(s) {
  if (!s) return null
  s = s.trim().replace(/^#/, '')
  if (s.startsWith('0x') || s.startsWith('0X')) return parseInt(s, 16)
  if (s.startsWith('-')) return parseInt(s, 10)
  const n = parseInt(s, 10)
  return isNaN(n) ? null : n
}

/**
 * Parse ARM64 memory operand: [xN] or [xN, #offset]
 * Returns { base: register number, offset: number } or null.
 */
function parseMemArm(s) {
  if (!s) return null
  s = s.replace(/[\[\]]/g, '').trim()

  // [xN, #offset]
  let m = s.match(/^([xw]\d+|sp)\s+#?\s*(-?\d+|0x[0-9a-fA-F]+)$/i)
  if (m) {
    const reg = parseReg(m[1])
    const off = parseImm(m[2])
    if (reg && off !== null) return { base: reg.n, offset: off }
  }

  // [xN]
  const reg = parseReg(s)
  if (reg) return { base: reg.n, offset: 0 }

  return null
}

// ══════════════════════════════════════
// ── Guard Rail: Binary Safety Scanner
// ══════════════════════════════════════

const MAX_BINARY_SIZE = 16384  // 16KB

function scanBinaryARM(bytes, opts = {}) {
  const warnings = []
  const errors = []

  // Size check
  if (bytes.length > MAX_BINARY_SIZE) {
    errors.push(`Binary too large: ${bytes.length} bytes (max ${MAX_BINARY_SIZE})`)
  }

  // Must be 4-byte aligned
  if (bytes.length % 4 !== 0) {
    errors.push(`Binary size ${bytes.length} is not 4-byte aligned`)
  }

  // Check last instruction is ret (0xD65F03C0)
  if (bytes.length >= 4) {
    const last = bytes[bytes.length - 4]
      | (bytes[bytes.length - 3] << 8)
      | (bytes[bytes.length - 2] << 16)
      | (bytes[bytes.length - 1] << 24)
    if ((last >>> 0) !== 0xD65F03C0) {
      warnings.push('Binary does not end with RET (0xD65F03C0) — may not return to caller')
    }
  }

  // Scan each instruction word
  for (let i = 0; i + 3 < bytes.length; i += 4) {
    const w = (bytes[i] | (bytes[i+1] << 8) | (bytes[i+2] << 16) | (bytes[i+3] << 24)) >>> 0

    // SVC — supervisor call: encoding 0xD4000001, mask top bits
    // SVC #imm16 → 1101_0100_000x_xxxx_xxxx_xxxx_xxx0_0001
    if (((w & 0xFFE0001F) >>> 0) === (0xD4000001 >>> 0)) {
      errors.push(`Offset ${i}: SVC (supervisor call) — blocked`)
    }

    // HLT — halt: 0xD4400000 pattern
    // HLT #imm16 → 1101_0100_010x_xxxx_xxxx_xxxx_xxx0_0000
    if (((w & 0xFFE0001F) >>> 0) === (0xD4400000 >>> 0)) {
      errors.push(`Offset ${i}: HLT (halt) — blocked`)
    }
  }

  return {
    safe: errors.length === 0,
    errors,
    warnings,
    size: bytes.length,
  }
}

function assembleAndValidateARM(source, opts = {}) {
  const bytes = assembleARM(source)
  const scan = scanBinaryARM(Array.from(bytes), opts)

  if (!scan.safe) {
    const err = new Error(`Assembly rejected: ${scan.errors.join('; ')}`)
    err.scan = scan
    throw err
  }

  return { binary: bytes, scan }
}

module.exports = { assembleARM, scanBinaryARM, assembleAndValidateARM, MAX_BINARY_SIZE }
