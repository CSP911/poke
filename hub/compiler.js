/**
 * POKE Hub — compilation: compileAssembly, compileAssemblyARM, runImageCode
 */

const fs = require('fs')
const { execSync } = require('child_process')
const path = require('path')
const { log } = require('./logger')

let _asmModule = null
function getAsmModule() {
  if (!_asmModule) _asmModule = require(path.join(__dirname, 'assembler', 'asm.js'))
  return _asmModule
}

// ── x86 assembly compile + safety scan ──
async function compileAssembly(asm) {
  try {
    const { assembleAndValidate, assemble, scanBinary } = getAsmModule()

    // Try assembleAndValidate first (includes guard rail)
    try {
      const { binary, scan } = assembleAndValidate(asm)
      if (scan.warnings.length > 0) {
        scan.warnings.forEach(w => log.warn(`[guard] ${w}`))
      }
      log.info(`[asm.js] assembled ${binary.length} bytes (safe)`)
      return binary
    } catch (guardErr) {
      // Guard rail rejected it
      if (guardErr.scan) {
        guardErr.scan.errors.forEach(e => log.error(`[guard] BLOCKED: ${e}`))
        log.error(`[guard] Assembly rejected by safety scanner`)
        return null
      }
      // Not a guard error, just assembly parse failure — try nasm fallback
      throw guardErr
    }
  } catch (e) {
    log.warn(`[asm.js] mini assembler failed: ${e.message}`)
    // fallback to nasm + manual scan
    try {
      fs.writeFileSync('/tmp/poke_gen.asm', asm)
      execSync('nasm -f bin -o /tmp/poke_gen.bin /tmp/poke_gen.asm 2>&1')
      const bin = fs.readFileSync('/tmp/poke_gen.bin')

      // Still scan nasm output for safety
      const { scanBinary } = getAsmModule()
      const scan = scanBinary(Array.from(bin))
      if (!scan.safe) {
        scan.errors.forEach(e => log.error(`[guard] BLOCKED: ${e}`))
        log.error('[guard] nasm output rejected by safety scanner')
        return null
      }

      log.info(`[nasm] compiled ${bin.length} bytes (safe)`)
      return bin
    } catch (e2) {
      log.error(`[nasm fallback] ${e2.message}`)
      return null
    }
  }
}

// ── ARM64 assembly compile + safety scan ──
async function compileAssemblyARM(asm) {
  // Try built-in ARM assembler first
  try {
    const { assembleAndValidateARM } = require(path.join(__dirname, 'assembler', 'asm_arm.js'))
    const { binary, scan } = assembleAndValidateARM(asm)
    if (scan.warnings.length > 0) {
      scan.warnings.forEach(w => log.warn(`[arm-guard] ${w}`))
    }
    log.info(`[asm_arm.js] assembled ${binary.length} bytes (safe)`)
    return binary
  } catch (e) {
    if (e.scan) {
      e.scan.errors.forEach(err => log.error(`[arm-guard] BLOCKED: ${err}`))
      log.error('[arm-guard] ARM assembly rejected by safety scanner')
      return null
    }
    log.warn(`[asm_arm.js] failed: ${e.message}`)
  }

  // Fallback to aarch64-elf-as
  const tmpAsm = '/tmp/poke_arm.s'
  const tmpObj = '/tmp/poke_arm.o'
  const tmpBin = '/tmp/poke_arm.bin'

  fs.writeFileSync(tmpAsm, asm)
  try {
    execSync(`aarch64-elf-as -o ${tmpObj} ${tmpAsm} 2>&1`)
    execSync(`aarch64-elf-objcopy -O binary ${tmpObj} ${tmpBin} 2>&1`)
    const bin = fs.readFileSync(tmpBin)

    // Scan external assembler output too
    const { scanBinaryARM } = require(path.join(__dirname, 'assembler', 'asm_arm.js'))
    const scan = scanBinaryARM(Array.from(bin))
    if (!scan.safe) {
      scan.errors.forEach(err => log.error(`[arm-guard] BLOCKED: ${err}`))
      return null
    }

    log.info(`[aarch64-elf-as] compiled ${bin.length} bytes (safe)`)
    return bin
  } catch (e) {
    log.error(`[arm-as fallback] ${e.message}`)
    return null
  }
}

// ── Run image generation code -> IMG binary ──
function runImageCode(code) {
  const tmpFile = '/tmp/poke_draw.js'

  const wrapped = code + `
;(function() {
  const m = module.exports;
  const out = { W: m.W, H: m.H, pixels: m.pixels.toString('base64') };
  process.stdout.write(JSON.stringify(out));
})();
`
  fs.writeFileSync(tmpFile, wrapped)

  try {
    const result = execSync(`node ${tmpFile}`, { timeout: 5000, maxBuffer: 1024 * 1024 })
    const json = JSON.parse(result.toString())
    const pixels = Buffer.from(json.pixels, 'base64')
    const W = json.W
    const H = json.H

    // IMG protocol: "IMG" + width(2 LE) + height(2 LE) + RGB
    const header = Buffer.alloc(7)
    header[0] = 0x49; header[1] = 0x4D; header[2] = 0x47
    header.writeUInt16LE(W, 3)
    header.writeUInt16LE(H, 5)

    return { width: W, height: H, body: Buffer.concat([header, pixels]) }
  } catch (e) {
    log.error(`[draw] execution error: ${e.message}`)
    return null
  }
}

// ── RISC-V assembly compile ──
let _rvModule = null
function getRVModule() {
  if (!_rvModule) _rvModule = require(path.join(__dirname, 'assembler', 'asm_rv.js'))
  return _rvModule
}

async function compileAssemblyRV(asm) {
  try {
    const { assembleRV } = getRVModule()
    const binary = assembleRV(asm)
    log.info(`[asm_rv.js] assembled ${binary.length} bytes`)
    return binary
  } catch (e) {
    log.warn(`[asm_rv.js] failed: ${e.message}`)
    return null
  }
}

// ── ARMv6 assembly compile ──
let _armv6Module = null
function getARMv6Module() {
  if (!_armv6Module) _armv6Module = require(path.join(__dirname, 'assembler', 'asm_armv6.js'))
  return _armv6Module
}

async function compileAssemblyARMv6(asm) {
  try {
    const { assembleAndValidateARMv6 } = getARMv6Module()
    const { binary, scan } = assembleAndValidateARMv6(asm)
    if (scan.warnings.length > 0) {
      scan.warnings.forEach(w => log.warn(`[armv6-guard] ${w}`))
    }
    log.info(`[asm_armv6.js] assembled ${binary.length} bytes (safe)`)
    return binary
  } catch (e) {
    if (e.scan) {
      e.scan.errors.forEach(err => log.error(`[armv6-guard] BLOCKED: ${err}`))
      log.error('[armv6-guard] ARMv6 assembly rejected by safety scanner')
      return null
    }
    log.warn(`[asm_armv6.js] failed: ${e.message}`)

    // Fallback to arm-none-eabi-as
    const tmpAsm = '/tmp/poke_armv6.s'
    const tmpObj = '/tmp/poke_armv6.o'
    const tmpBin = '/tmp/poke_armv6.bin'

    fs.writeFileSync(tmpAsm, asm)
    try {
      execSync(`arm-none-eabi-as -march=armv6 -o ${tmpObj} ${tmpAsm} 2>&1`)
      execSync(`arm-none-eabi-objcopy -O binary ${tmpObj} ${tmpBin} 2>&1`)
      const bin = fs.readFileSync(tmpBin)

      const { scanBinaryARMv6 } = getARMv6Module()
      const scan = scanBinaryARMv6(Array.from(bin))
      if (!scan.safe) {
        scan.errors.forEach(err => log.error(`[armv6-guard] BLOCKED: ${err}`))
        return null
      }

      log.info(`[arm-none-eabi-as] compiled ${bin.length} bytes (safe)`)
      return bin
    } catch (e2) {
      log.error(`[armv6-as fallback] ${e2.message}`)
      return null
    }
  }
}

module.exports = {
  compileAssembly,
  compileAssemblyARM,
  compileAssemblyRV,
  compileAssemblyARMv6,
  runImageCode,
}
