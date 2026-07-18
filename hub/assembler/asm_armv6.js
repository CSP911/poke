/**
 * POKE Mini Assembler — ARMv6 (ARM 32-bit, ARM mode) assembler
 *
 * Converts LLM-generated ARMv6 assembly into machine code bytes.
 * No external tools required. Used via require() from hub.
 *
 * Target: ARM1176JZF-S (Raspberry Pi Zero W) — ARMv6, ARM mode.
 * All instructions are exactly 4 bytes, little-endian.
 *
 * Supported: mov, mvn, add, sub, rsb, mul, and, orr, eor, bic,
 *            cmp, cmn, tst, teq, ldr, str, ldrb, strb, ldrh, strh,
 *            ldr Rd, =value (literal pool), push, pop,
 *            b, bl, bx, beq/bne/blt/bge/bgt/ble/bhi/blo/bcs/bcc/bmi/bpl/bvs/bvc/bal,
 *            lsl, lsr, asr (as shifts), nop, swi/svc (for scanner)
 */

// ── Condition codes ──
const COND = {
  eq: 0x0, ne: 0x1, cs: 0x2, hs: 0x2, cc: 0x3, lo: 0x3,
  mi: 0x4, pl: 0x5, vs: 0x6, vc: 0x7,
  hi: 0x8, ls: 0x9, ge: 0xA, lt: 0xB, gt: 0xC, le: 0xD, al: 0xE,
}

// ── Data processing opcodes ──
const DP_OPS = {
  and: 0x0, eor: 0x1, sub: 0x2, rsb: 0x3,
  add: 0x4, adc: 0x5, sbc: 0x6, rsc: 0x7,
  tst: 0x8, teq: 0x9, cmp: 0xA, cmn: 0xB,
  orr: 0xC, mov: 0xD, bic: 0xE, mvn: 0xF,
}

// ── Register map ──
function parseReg(s) {
  if (!s) return null
  s = s.trim().toLowerCase()
  if (s === 'sp') return 13
  if (s === 'lr') return 14
  if (s === 'pc') return 15
  const m = s.match(/^r(\d+)$/)
  if (!m) return null
  const n = parseInt(m[1], 10)
  if (n < 0 || n > 15) return null
  return n
}

function parseImm(s) {
  if (!s) return null
  s = s.trim().replace(/^#/, '')
  if (s.startsWith('0x') || s.startsWith('0X')) return parseInt(s, 16)
  if (s.startsWith('-0x') || s.startsWith('-0X')) return -parseInt(s.slice(1), 16)
  if (s.startsWith('-')) return parseInt(s, 10)
  const n = parseInt(s, 10)
  return isNaN(n) ? null : n
}

/**
 * Encode an immediate value as ARM rotated 8-bit immediate.
 * Returns { imm8, rotate } or null if not encodable.
 */
function encodeImm(val) {
  val = val >>> 0  // unsigned 32-bit
  if (val <= 0xFF) return { imm8: val, rotate: 0 }
  for (let rot = 1; rot < 16; rot++) {
    const shift = rot * 2
    const candidate = ((val << shift) | (val >>> (32 - shift))) >>> 0
    if (candidate <= 0xFF) {
      return { imm8: candidate, rotate: rot }
    }
  }
  return null
}

/**
 * Parse memory operand: [Rn] or [Rn, #offset] or [Rn, Rm]
 * Returns { base, offset, regOffset } or null
 */
function parseMem(parts) {
  const joined = parts.join(' ').replace(/,/g, ' ').trim()
  const inner = joined.replace(/[\[\]!]/g, '').trim()
  const tokens = inner.split(/\s+/)

  const base = parseReg(tokens[0])
  if (base === null) return null

  if (tokens.length === 1) return { base, offset: 0, regOffset: null }

  const regOff = parseReg(tokens[1])
  if (regOff !== null) return { base, offset: 0, regOffset: regOff }

  const off = parseImm(tokens[1])
  if (off !== null) return { base, offset: off, regOffset: null }

  return null
}

/**
 * Parse register list for PUSH/POP: {r0, r1, r4-r7, lr}
 * Returns bitmask (16-bit) or null
 */
function parseRegList(parts) {
  const joined = parts.join(' ').replace(/[{}]/g, '').replace(/,/g, ' ').trim()
  if (!joined) return null
  const tokens = joined.split(/\s+/)
  let mask = 0

  for (const tok of tokens) {
    // Range: r4-r7
    const rangeMatch = tok.match(/^(r\d+|sp|lr|pc)-(r\d+|sp|lr|pc)$/i)
    if (rangeMatch) {
      const lo = parseReg(rangeMatch[1])
      const hi = parseReg(rangeMatch[2])
      if (lo === null || hi === null || lo > hi) return null
      for (let i = lo; i <= hi; i++) mask |= (1 << i)
      continue
    }
    const r = parseReg(tok)
    if (r === null) return null
    mask |= (1 << r)
  }
  return mask
}

// ══════════════════════════════════════
// ── Main assembler
// ══════════════════════════════════════

function assembleARMv6_impl(source) {
  let lines = source.split('\n')
    .map(l => l.replace(/@.*$/, '').replace(/\/\/.*$/, '').replace(/;.*$/, '').trim())
    .filter(l => l.length > 0)

  // Expand "label: instruction" → two lines, skip directives
  const expanded = []
  for (const l of lines) {
    if (l.startsWith('.')) continue
    const m = l.match(/^(\w+):\s+(.+)$/)
    if (m) { expanded.push(m[1] + ':'); expanded.push(m[2]) }
    else expanded.push(l)
  }
  lines = expanded

  const buf = []
  const labels = {}
  const fixups = []
  const litFixups = []  // { pos (word index), rd, value (u32) }

  for (const raw of lines) {
    const line = raw.replace(/,/g, ' ').replace(/\s+/g, ' ').trim()

    if (line.endsWith(':')) {
      labels[line.slice(0, -1)] = buf.length
      continue
    }

    const parts = line.split(' ')
    const op = parts[0].toLowerCase()
    const cond = 0xE  // always

    // ── NOP ──
    if (op === 'nop') { buf.push(0xE1A00000); continue }

    // ── BX Rm ──
    if (op === 'bx') {
      const rm = parseReg(parts[1])
      if (rm === null) throw new Error(`Bad bx: "${raw}"`)
      buf.push(((cond << 28) | 0x012FFF10 | rm) >>> 0)
      continue
    }

    // ── B / BL (with optional condition) ──
    const branchMatch = op.match(/^(bl?)(eq|ne|cs|hs|cc|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le|al)?$/)
    if (branchMatch && parts.length === 2 && !['bic'].includes(op)) {
      const isLink = branchMatch[1] === 'bl'
      const condStr = branchMatch[2]
      const brCond = condStr ? COND[condStr] : 0xE

      const targetReg = parseReg(parts[1])
      if (targetReg !== null && !isLink) {
        buf.push(((brCond << 28) | 0x012FFF10 | targetReg) >>> 0)
        continue
      }

      const linkBit = isLink ? 0x0B000000 : 0x0A000000
      if (labels[parts[1]] !== undefined) {
        const offset = labels[parts[1]] - buf.length - 2
        buf.push(((brCond << 28) | linkBit | (offset & 0x00FFFFFF)) >>> 0)
      } else {
        fixups.push({ pos: buf.length, label: parts[1], type: isLink ? 'bl' : 'b', cond: brCond })
        buf.push(0)
      }
      continue
    }

    // ── PUSH ──
    if (op === 'push') {
      const mask = parseRegList(parts.slice(1))
      if (mask === null) throw new Error(`Bad push: "${raw}"`)
      buf.push((0xE92D0000 | mask) >>> 0)
      continue
    }

    // ── POP ──
    if (op === 'pop') {
      const mask = parseRegList(parts.slice(1))
      if (mask === null) throw new Error(`Bad pop: "${raw}"`)
      buf.push((0xE8BD0000 | mask) >>> 0)
      continue
    }

    // ── LDR Rd, =value ──
    if (op === 'ldr' && parts.length >= 3 && parts[2] && parts[2].startsWith('=')) {
      const rd = parseReg(parts[1])
      if (rd === null) throw new Error(`Bad ldr =: "${raw}"`)
      const val = parseImm(parts[2].slice(1))
      if (val === null) throw new Error(`Bad ldr = value: "${raw}"`)

      // Try MOV encoding first for small values
      const enc = encodeImm(val >>> 0)
      if (enc) {
        buf.push(((cond << 28) | (1 << 25) | (DP_OPS['mov'] << 21) | (rd << 12) | (enc.rotate << 8) | enc.imm8) >>> 0)
        continue
      }
      // Try MVN for bitwise complement
      const encN = encodeImm((~val) >>> 0)
      if (encN) {
        buf.push(((cond << 28) | (1 << 25) | (DP_OPS['mvn'] << 21) | (rd << 12) | (encN.rotate << 8) | encN.imm8) >>> 0)
        continue
      }

      // Needs literal pool
      litFixups.push({ pos: buf.length, rd, value: val >>> 0 })
      buf.push(0)  // placeholder
      continue
    }

    // ── LDR / STR / LDRB / STRB / LDRH / STRH ──
    if (op === 'ldr' || op === 'str' || op === 'ldrb' || op === 'strb' || op === 'ldrh' || op === 'strh') {
      const rt = parseReg(parts[1])
      if (rt === null) throw new Error(`Bad ${op}: "${raw}"`)
      const mem = parseMem(parts.slice(2))
      if (!mem) throw new Error(`Bad ${op} memory: "${raw}"`)

      if (op === 'ldrh' || op === 'strh') {
        const isLoad = op === 'ldrh'
        const off = mem.offset
        const u = off >= 0 ? 1 : 0
        const absOff = Math.abs(off)
        const imm4H = (absOff >> 4) & 0xF
        const imm4L = absOff & 0xF
        const base = (cond << 28) | (1 << 24) | (u << 23) | (1 << 22) | (isLoad ? (1 << 20) : 0)
        buf.push((base | (mem.base << 16) | (rt << 12) | (imm4H << 8) | 0xB0 | imm4L) >>> 0)
        continue
      }

      const isByte = op === 'ldrb' || op === 'strb'
      const isLoad = op === 'ldr' || op === 'ldrb'

      if (mem.regOffset !== null) {
        const base = (cond << 28) | (0x07 << 24) | (1 << 23) | (isByte ? (1 << 22) : 0) | (isLoad ? (1 << 20) : 0)
        buf.push((base | (mem.base << 16) | (rt << 12) | mem.regOffset) >>> 0)
        continue
      }

      const off = mem.offset
      const u = off >= 0 ? 1 : 0
      const absOff = Math.abs(off)
      if (absOff > 4095) throw new Error(`Offset too large (max ±4095): "${raw}"`)
      const base = (cond << 28) | (0x05 << 24) | (u << 23) | (isByte ? (1 << 22) : 0) | (isLoad ? (1 << 20) : 0)
      buf.push((base | (mem.base << 16) | (rt << 12) | (absOff & 0xFFF)) >>> 0)
      continue
    }

    // ── MUL ──
    if (op === 'mul') {
      const rd = parseReg(parts[1])
      const rm = parseReg(parts[2])
      const rs = parseReg(parts[3])
      if (rd === null || rm === null || rs === null) throw new Error(`Bad mul: "${raw}"`)
      buf.push(((cond << 28) | (rd << 16) | (rs << 8) | 0x90 | rm) >>> 0)
      continue
    }

    // ── MLA ──
    if (op === 'mla') {
      const rd = parseReg(parts[1])
      const rm = parseReg(parts[2])
      const rs = parseReg(parts[3])
      const rn = parseReg(parts[4])
      if (rd === null || rm === null || rs === null || rn === null) throw new Error(`Bad mla: "${raw}"`)
      buf.push(((cond << 28) | 0x00200090 | (rd << 16) | (rn << 12) | (rs << 8) | rm) >>> 0)
      continue
    }

    // ── LSL / LSR / ASR ──
    if (op === 'lsl' || op === 'lsr' || op === 'asr') {
      const rd = parseReg(parts[1])
      const rm = parseReg(parts[2])
      if (rd === null || rm === null) throw new Error(`Bad ${op}: "${raw}"`)
      const shType = op === 'lsl' ? 0 : op === 'lsr' ? 1 : 2

      // Check if 3rd operand is register or immediate
      const rs = parseReg(parts[3])
      if (rs !== null) {
        // Register shift: MOV Rd, Rm, LSL Rs
        buf.push(((cond << 28) | 0x01A00000 | (rd << 12) | (rs << 8) | (shType << 5) | 0x10 | rm) >>> 0)
        continue
      }
      const shamt = parseImm(parts[3])
      if (shamt === null) throw new Error(`Bad ${op}: "${raw}"`)
      buf.push(((cond << 28) | 0x01A00000 | (rd << 12) | (shamt << 7) | (shType << 5) | rm) >>> 0)
      continue
    }

    // ── CMP, CMN, TST, TEQ ──
    if (op === 'cmp' || op === 'cmn' || op === 'tst' || op === 'teq') {
      const rn = parseReg(parts[1])
      if (rn === null) throw new Error(`Bad ${op}: "${raw}"`)
      const dpOp = DP_OPS[op]

      const rm = parseReg(parts[2])
      if (rm !== null) {
        buf.push(((cond << 28) | (dpOp << 21) | (1 << 20) | (rn << 16) | rm) >>> 0)
        continue
      }
      const immVal = parseImm(parts[2])
      if (immVal === null) throw new Error(`Bad ${op}: "${raw}"`)
      const enc = encodeImm(immVal >>> 0)
      if (!enc) throw new Error(`Cannot encode immediate ${immVal} for ${op}: "${raw}"`)
      buf.push(((cond << 28) | (1 << 25) | (dpOp << 21) | (1 << 20) | (rn << 16) | (enc.rotate << 8) | enc.imm8) >>> 0)
      continue
    }

    // ── MOV / MVN ──
    if (op === 'mov' || op === 'mvn') {
      const rd = parseReg(parts[1])
      if (rd === null) throw new Error(`Bad ${op}: "${raw}"`)
      const dpOp = DP_OPS[op]

      // MOV Rd, Rm, shift #imm
      if (parts.length >= 5) {
        const rm = parseReg(parts[2])
        const shiftOp = parts[3] ? parts[3].toLowerCase() : null
        if (rm !== null && (shiftOp === 'lsl' || shiftOp === 'lsr' || shiftOp === 'asr' || shiftOp === 'ror')) {
          const shamt = parseImm(parts[4])
          if (shamt === null) throw new Error(`Bad shift: "${raw}"`)
          const shType = shiftOp === 'lsl' ? 0 : shiftOp === 'lsr' ? 1 : shiftOp === 'asr' ? 2 : 3
          buf.push(((cond << 28) | (dpOp << 21) | (rd << 12) | (shamt << 7) | (shType << 5) | rm) >>> 0)
          continue
        }
      }

      const rm = parseReg(parts[2])
      if (rm !== null) {
        buf.push(((cond << 28) | (dpOp << 21) | (rd << 12) | rm) >>> 0)
        continue
      }

      const immVal = parseImm(parts[2])
      if (immVal === null) throw new Error(`Bad ${op}: "${raw}"`)
      const enc = encodeImm(immVal >>> 0)
      if (!enc) throw new Error(`Cannot encode immediate ${immVal} for ${op}. Use ldr Rd, =${immVal}: "${raw}"`)
      buf.push(((cond << 28) | (1 << 25) | (dpOp << 21) | (rd << 12) | (enc.rotate << 8) | enc.imm8) >>> 0)
      continue
    }

    // ── ADD, SUB, AND, ORR, EOR, BIC, RSB, ADC, SBC, RSC ──
    if (DP_OPS[op] !== undefined) {
      const rd = parseReg(parts[1])
      const rn = parseReg(parts[2])
      if (rd === null || rn === null) throw new Error(`Bad ${op}: "${raw}"`)
      const dpOp = DP_OPS[op]

      const rm = parseReg(parts[3])
      if (rm !== null) {
        let shift = 0
        if (parts.length >= 6) {
          const shiftOp = parts[4] ? parts[4].toLowerCase() : null
          const shamt = parts[5] ? parseImm(parts[5]) : null
          if (shiftOp && shamt !== null) {
            const shType = shiftOp === 'lsl' ? 0 : shiftOp === 'lsr' ? 1 : shiftOp === 'asr' ? 2 : 3
            shift = (shamt << 7) | (shType << 5)
          }
        }
        buf.push(((cond << 28) | (dpOp << 21) | (rn << 16) | (rd << 12) | shift | rm) >>> 0)
        continue
      }

      const immVal = parseImm(parts[3])
      if (immVal === null) throw new Error(`Bad ${op}: "${raw}"`)

      // Handle negative immediate for ADD/SUB
      if (op === 'sub' && immVal < 0) {
        const enc = encodeImm((-immVal) >>> 0)
        if (enc) {
          buf.push(((cond << 28) | (1 << 25) | (DP_OPS['add'] << 21) | (rn << 16) | (rd << 12) | (enc.rotate << 8) | enc.imm8) >>> 0)
          continue
        }
      }
      if (op === 'add' && immVal < 0) {
        const enc = encodeImm((-immVal) >>> 0)
        if (enc) {
          buf.push(((cond << 28) | (1 << 25) | (DP_OPS['sub'] << 21) | (rn << 16) | (rd << 12) | (enc.rotate << 8) | enc.imm8) >>> 0)
          continue
        }
      }

      const enc = encodeImm(immVal >>> 0)
      if (!enc) throw new Error(`Cannot encode immediate ${immVal} for ${op}: "${raw}"`)
      buf.push(((cond << 28) | (1 << 25) | (dpOp << 21) | (rn << 16) | (rd << 12) | (enc.rotate << 8) | enc.imm8) >>> 0)
      continue
    }

    // ── SWI / SVC ──
    if (op === 'swi' || op === 'svc') {
      const immVal = parseImm(parts[1]) || 0
      buf.push(((cond << 28) | 0x0F000000 | (immVal & 0x00FFFFFF)) >>> 0)
      continue
    }

    throw new Error(`Unknown instruction: "${raw}"`)
  }

  // ── Emit literal pool (after all instructions) ──
  const poolMap = new Map()  // value → word index
  for (const lit of litFixups) {
    let poolIdx
    if (poolMap.has(lit.value)) {
      poolIdx = poolMap.get(lit.value)
    } else {
      poolIdx = buf.length
      poolMap.set(lit.value, poolIdx)
      buf.push(lit.value)
    }

    // Fixup: LDR Rd, [PC, #offset]
    // At execution, PC = instruction_addr + 8
    const pcOffset = (poolIdx - lit.pos - 2) * 4
    if (pcOffset < 0 || pcOffset > 4095) throw new Error(`Literal pool too far from LDR (offset=${pcOffset}, max 4095)`)
    // LDR Rd, [PC, #offset]: 0xE59F0000 | (Rd << 12) | offset
    buf[lit.pos] = ((0xE << 28) | (0x05 << 24) | (1 << 23) | (1 << 20) | (15 << 16) | (lit.rd << 12) | (pcOffset & 0xFFF)) >>> 0
  }

  // ── 2nd pass: fixup branches ──
  for (const f of fixups) {
    const target = labels[f.label]
    if (target === undefined) throw new Error(`Unknown label: ${f.label}`)
    const offset = target - f.pos - 2
    const linkBit = f.type === 'bl' ? 0x0B000000 : 0x0A000000
    buf[f.pos] = ((f.cond << 28) | linkBit | (offset & 0x00FFFFFF)) >>> 0
  }

  // Convert to bytes
  const bytes = Buffer.alloc(buf.length * 4)
  for (let i = 0; i < buf.length; i++) {
    bytes.writeUInt32LE(buf[i] >>> 0, i * 4)
  }
  return bytes
}

// ══════════════════════════════════════
// ── Guard Rail: Binary Safety Scanner
// ══════════════════════════════════════

const MAX_BINARY_SIZE = 16384  // 16KB

function scanBinaryARMv6(bytes, opts = {}) {
  const warnings = []
  const errors = []

  if (bytes.length > MAX_BINARY_SIZE) {
    errors.push(`Binary too large: ${bytes.length} bytes (max ${MAX_BINARY_SIZE})`)
  }

  if (bytes.length % 4 !== 0) {
    errors.push(`Binary size ${bytes.length} is not 4-byte aligned`)
  }

  // Check last instruction is BX LR (0xE12FFF1E) or MOV PC, LR (0xE1A0F00E) or POP {pc} contains bit 15
  if (bytes.length >= 4) {
    const last = (bytes[bytes.length - 4]
      | (bytes[bytes.length - 3] << 8)
      | (bytes[bytes.length - 2] << 16)
      | (bytes[bytes.length - 1] << 24)) >>> 0

    const isBxLr = last === (0xE12FFF1E >>> 0)
    const isMovPcLr = last === (0xE1A0F00E >>> 0)
    const isPopPc = ((last & 0xFFFF0000) >>> 0) === (0xE8BD0000 >>> 0) && (last & 0x8000)  // POP {.., pc}

    if (!isBxLr && !isMovPcLr && !isPopPc) {
      warnings.push('Binary does not end with BX LR, MOV PC LR, or POP {pc} — may not return to caller')
    }
  }

  // Scan each instruction
  for (let i = 0; i + 3 < bytes.length; i += 4) {
    const w = (bytes[i] | (bytes[i+1] << 8) | (bytes[i+2] << 16) | (bytes[i+3] << 24)) >>> 0

    // SWI/SVC: cond 1111 xxxxxxxx
    if ((w & 0x0F000000) === 0x0F000000 && ((w >> 28) & 0xF) <= 0xE) {
      // Check it's actually SWI (bits [27:24] = 1111)
      if (((w >> 24) & 0xF) === 0xF) {
        errors.push(`Offset ${i}: SWI/SVC (software interrupt) — blocked`)
      }
    }

    // MCR/MRC to coprocessor (could mess with system state)
    // MCR: cond 1110 xxx0 CRn Rd cp xxx1 CRm
    if (((w >> 24) & 0xF) === 0xE && ((w >> 4) & 1) === 1 && !opts.allowCoprocessor) {
      // This is a coprocessor instruction — warn but don't block (needed for cache flush)
      // Only block writes to system control coprocessor that aren't cache ops
    }
  }

  return {
    safe: errors.length === 0,
    errors,
    warnings,
    size: bytes.length,
  }
}

function assembleAndValidateARMv6(source, opts = {}) {
  const bytes = assembleARMv6_impl(source)
  const scan = scanBinaryARMv6(Array.from(bytes), opts)

  if (!scan.safe) {
    const err = new Error(`Assembly rejected: ${scan.errors.join('; ')}`)
    err.scan = scan
    throw err
  }

  return { binary: bytes, scan }
}

module.exports = {
  assembleARMv6: assembleARMv6_impl,
  scanBinaryARMv6,
  assembleAndValidateARMv6,
  encodeImm,
  MAX_BINARY_SIZE,
}
