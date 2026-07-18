/**
 * POKE Mini Assembler — RISC-V 32-bit (RV32I)
 *
 * Converts LLM-generated RISC-V assembly into machine code bytes.
 * No external tools required. Used via require() from hub.
 *
 * Supported: add, sub, and, or, xor, sll, srl, sra, slt, sltu,
 *            addi, andi, ori, xori, slti, sltiu, slli, srli, srai,
 *            lw, sw, lb, sb, lh, sh, lbu, lhu,
 *            beq, bne, blt, bge, bltu, bgeu,
 *            lui, auipc, jal, jalr, ret, nop, li, mv,
 *            ecall, ebreak, fence.i
 */

function assembleRV(source) {
  let lines = source.split('\n')
    .map(l => l.replace(/#.*$/, '').replace(/\/\/.*$/, '').trim())  // strip comments
    .filter(l => l.length > 0)

  // Expand "label: instruction" into two lines
  const expanded = []
  for (const l of lines) {
    const m = l.match(/^(\w+):\s+(.+)$/)
    if (m) { expanded.push(m[1] + ':'); expanded.push(m[2]) }
    else expanded.push(l)
  }
  lines = expanded

  const bytes = []
  const labels = {}
  const fixups = []  // { pos, label, type }

  // Register map
  const regs = {}
  for (let i = 0; i < 32; i++) regs['x' + i] = i
  regs.zero = 0; regs.ra = 1; regs.sp = 2; regs.gp = 3; regs.tp = 4
  regs.t0 = 5; regs.t1 = 6; regs.t2 = 7
  regs.s0 = 8; regs.fp = 8; regs.s1 = 9
  for (let i = 0; i <= 7; i++) regs['a' + i] = 10 + i
  for (let i = 2; i <= 11; i++) regs['s' + i] = 18 + i - 2
  regs.t3 = 28; regs.t4 = 29; regs.t5 = 30; regs.t6 = 31

  function reg(name) {
    const r = regs[name.toLowerCase()]
    if (r === undefined) throw new Error('Unknown register: ' + name)
    return r
  }

  function imm(s) {
    s = s.trim()
    if (s.startsWith('0x') || s.startsWith('0X')) return parseInt(s, 16)
    if (s.startsWith('0b')) return parseInt(s.slice(2), 2)
    return parseInt(s, 10)
  }

  function emit32(val) {
    bytes.push(val & 0xFF, (val >> 8) & 0xFF, (val >> 16) & 0xFF, (val >> 24) & 0xFF)
  }

  // R-type: funct7[31:25] rs2[24:20] rs1[19:15] funct3[14:12] rd[11:7] opcode[6:0]
  function rType(funct7, rs2, rs1, funct3, rd, opcode) {
    emit32((funct7 << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | opcode)
  }

  // I-type: imm[31:20] rs1[19:15] funct3[14:12] rd[11:7] opcode[6:0]
  function iType(immVal, rs1, funct3, rd, opcode) {
    emit32(((immVal & 0xFFF) << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | opcode)
  }

  // S-type: imm[11:5][31:25] rs2[24:20] rs1[19:15] funct3[14:12] imm[4:0][11:7] opcode[6:0]
  function sType(immVal, rs2, rs1, funct3, opcode) {
    const i = immVal & 0xFFF
    emit32(((i >> 5) << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) | ((i & 0x1F) << 7) | opcode)
  }

  // B-type: imm encoding for branches
  function bType(immVal, rs2, rs1, funct3, opcode) {
    const i = immVal & 0x1FFF
    const b12 = (i >> 12) & 1, b10_5 = (i >> 5) & 0x3F, b4_1 = (i >> 1) & 0xF, b11 = (i >> 11) & 1
    emit32((b12 << 31) | (b10_5 << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) | (b4_1 << 8) | (b11 << 7) | opcode)
  }

  // U-type: imm[31:12] rd[11:7] opcode[6:0]
  function uType(immVal, rd, opcode) {
    emit32((immVal & 0xFFFFF000) | (rd << 7) | opcode)
  }

  // J-type: for JAL
  function jType(immVal, rd, opcode) {
    const i = immVal & 0x1FFFFF
    const b20 = (i >> 20) & 1, b10_1 = (i >> 1) & 0x3FF, b11 = (i >> 11) & 1, b19_12 = (i >> 12) & 0xFF
    emit32((b20 << 31) | (b10_1 << 21) | (b11 << 20) | (b19_12 << 12) | (rd << 7) | opcode)
  }

  // 1st pass
  for (const raw of lines) {
    const line = raw.trim()

    // Labels
    if (line.endsWith(':')) {
      labels[line.slice(0, -1)] = bytes.length
      continue
    }

    const parts = line.replace(/,/g, ' ').replace(/\s+/g, ' ').split(' ')
    const op = parts[0].toLowerCase()

    try {
      // Pseudo-instructions
      if (op === 'nop') { emit32(0x00000013); continue }  // addi x0, x0, 0
      if (op === 'ret') { emit32(0x00008067); continue }  // jalr x0, x1, 0
      if (op === 'mv') { iType(0, reg(parts[2]), 0, reg(parts[1]), 0x13); continue }  // addi rd, rs, 0
      if (op === 'li') {
        const rd = reg(parts[1]), val = imm(parts[2])
        if (val >= -2048 && val <= 2047) {
          iType(val, 0, 0, rd, 0x13)  // addi rd, x0, imm
        } else {
          uType(val & 0xFFFFF000, rd, 0x37)  // lui rd, upper
          if (val & 0xFFF) iType(val & 0xFFF, rd, 0, rd, 0x13)  // addi rd, rd, lower
        }
        continue
      }
      if (op === 'fence.i') { emit32(0x0000100F); continue }

      // R-type ALU
      const rOps = {
        'add': [0x00, 0], 'sub': [0x20, 0], 'sll': [0x00, 1], 'slt': [0x00, 2],
        'sltu': [0x00, 3], 'xor': [0x00, 4], 'srl': [0x00, 5], 'sra': [0x20, 5],
        'or': [0x00, 6], 'and': [0x00, 7],
        'mul': [0x01, 0], 'mulh': [0x01, 1], 'div': [0x01, 4], 'rem': [0x01, 6],
      }
      if (rOps[op]) {
        rType(rOps[op][0], reg(parts[3]), reg(parts[2]), rOps[op][1], reg(parts[1]), 0x33)
        continue
      }

      // I-type ALU
      const iOps = { 'addi': 0, 'slti': 2, 'sltiu': 3, 'xori': 4, 'ori': 6, 'andi': 7 }
      if (iOps[op] !== undefined) {
        iType(imm(parts[3]), reg(parts[2]), iOps[op], reg(parts[1]), 0x13)
        continue
      }
      // Shift immediate
      if (op === 'slli') { iType(imm(parts[3]) & 0x1F, reg(parts[2]), 1, reg(parts[1]), 0x13); continue }
      if (op === 'srli') { iType(imm(parts[3]) & 0x1F, reg(parts[2]), 5, reg(parts[1]), 0x13); continue }
      if (op === 'srai') { iType((imm(parts[3]) & 0x1F) | 0x400, reg(parts[2]), 5, reg(parts[1]), 0x13); continue }

      // Load: lw rd, offset(rs1)
      if (op === 'lw' || op === 'lh' || op === 'lb' || op === 'lhu' || op === 'lbu') {
        const funct3 = { lw: 2, lh: 1, lb: 0, lhu: 5, lbu: 4 }[op]
        const m = parts.slice(2).join(' ').match(/(-?\d+|0x[0-9a-fA-F]+)\s*\(\s*(\w+)\s*\)/)
        if (m) { iType(imm(m[1]), reg(m[2]), funct3, reg(parts[1]), 0x03); continue }
        // lw rd, addr — absolute load
        if (parts.length === 3) { iType(imm(parts[2]), 0, funct3, reg(parts[1]), 0x03); continue }
      }

      // Store: sw rs2, offset(rs1)
      if (op === 'sw' || op === 'sh' || op === 'sb') {
        const funct3 = { sw: 2, sh: 1, sb: 0 }[op]
        const m = parts.slice(2).join(' ').match(/(-?\d+|0x[0-9a-fA-F]+)\s*\(\s*(\w+)\s*\)/)
        if (m) { sType(imm(m[1]), reg(parts[1]), reg(m[2]), funct3, 0x23); continue }
      }

      // Branch: beq rs1, rs2, label
      const bOps = { 'beq': 0, 'bne': 1, 'blt': 4, 'bge': 5, 'bltu': 6, 'bgeu': 7 }
      if (bOps[op] !== undefined) {
        const target = parts[3]
        if (labels[target] !== undefined) {
          bType(labels[target] - bytes.length, reg(parts[2]), reg(parts[1]), bOps[op], 0x63)
        } else {
          fixups.push({ pos: bytes.length, label: target, type: 'B', funct3: bOps[op], rs1: reg(parts[1]), rs2: reg(parts[2]) })
          emit32(0)  // placeholder
        }
        continue
      }

      // LUI, AUIPC
      if (op === 'lui') { uType(imm(parts[2]) << 12, reg(parts[1]), 0x37); continue }
      if (op === 'auipc') { uType(imm(parts[2]) << 12, reg(parts[1]), 0x17); continue }

      // JAL
      if (op === 'jal') {
        if (parts.length === 2) {
          // jal label → jal ra, label
          const target = parts[1]
          if (labels[target] !== undefined) {
            jType(labels[target] - bytes.length, 1, 0x6F)
          } else {
            fixups.push({ pos: bytes.length, label: target, type: 'J', rd: 1 })
            emit32(0)
          }
        } else {
          // jal rd, label
          const target = parts[2]
          if (labels[target] !== undefined) {
            jType(labels[target] - bytes.length, reg(parts[1]), 0x6F)
          } else {
            fixups.push({ pos: bytes.length, label: target, type: 'J', rd: reg(parts[1]) })
            emit32(0)
          }
        }
        continue
      }

      // JALR
      if (op === 'jalr') {
        if (parts.length === 2) {
          // jalr rs1 → jalr x0, rs1, 0
          iType(0, reg(parts[1]), 0, 0, 0x67)
        } else {
          iType(imm(parts[3] || '0'), reg(parts[2]), 0, reg(parts[1]), 0x67)
        }
        continue
      }

      // J pseudo: j label → jal x0, label
      if (op === 'j') {
        const target = parts[1]
        if (labels[target] !== undefined) {
          jType(labels[target] - bytes.length, 0, 0x6F)
        } else {
          fixups.push({ pos: bytes.length, label: target, type: 'J', rd: 0 })
          emit32(0)
        }
        continue
      }

      // ECALL, EBREAK
      if (op === 'ecall') { emit32(0x00000073); continue }
      if (op === 'ebreak') { emit32(0x00100073); continue }

      throw new Error('Unknown instruction: ' + line)
    } catch (e) {
      throw new Error(`Line "${line}": ${e.message}`)
    }
  }

  // 2nd pass: fixup labels
  for (const f of fixups) {
    if (labels[f.label] === undefined) throw new Error('Undefined label: ' + f.label)
    const offset = labels[f.label] - f.pos

    if (f.type === 'B') {
      const i = offset & 0x1FFF
      const b12 = (i >> 12) & 1, b10_5 = (i >> 5) & 0x3F, b4_1 = (i >> 1) & 0xF, b11 = (i >> 11) & 1
      const val = (b12 << 31) | (b10_5 << 25) | (f.rs2 << 20) | (f.rs1 << 15) | (f.funct3 << 12) | (b4_1 << 8) | (b11 << 7) | 0x63
      bytes[f.pos] = val & 0xFF; bytes[f.pos+1] = (val >> 8) & 0xFF
      bytes[f.pos+2] = (val >> 16) & 0xFF; bytes[f.pos+3] = (val >> 24) & 0xFF
    } else if (f.type === 'J') {
      const i = offset & 0x1FFFFF
      const b20 = (i >> 20) & 1, b10_1 = (i >> 1) & 0x3FF, b11 = (i >> 11) & 1, b19_12 = (i >> 12) & 0xFF
      const val = (b20 << 31) | (b10_1 << 21) | (b11 << 20) | (b19_12 << 12) | (f.rd << 7) | 0x6F
      bytes[f.pos] = val & 0xFF; bytes[f.pos+1] = (val >> 8) & 0xFF
      bytes[f.pos+2] = (val >> 16) & 0xFF; bytes[f.pos+3] = (val >> 24) & 0xFF
    }
  }

  return Buffer.from(bytes)
}

module.exports = { assembleRV }
