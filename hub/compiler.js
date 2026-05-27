/**
 * POKE Hub — compilation: compileAssembly, compileAssemblyARM, runImageCode
 */

const fs = require('fs')
const { execSync } = require('child_process')
const path = require('path')
const { log } = require('./logger')

let _assemble = null
function getAssemble() {
  if (!_assemble) _assemble = require(path.join(__dirname, '..', 'asm.js')).assemble
  return _assemble
}

// ── NASM compile (x86) ──
async function compileAssembly(asm) {
  try {
    const assemble = getAssemble()
    const bin = assemble(asm)
    log.info(`[asm.js] assembled ${bin.length} bytes`)
    return bin
  } catch (e) {
    log.warn(`[asm.js] mini assembler failed: ${e.message}`)
    // fallback to nasm
    try {
      fs.writeFileSync('/tmp/poke_gen.asm', asm)
      execSync('nasm -f bin -o /tmp/poke_gen.bin /tmp/poke_gen.asm 2>&1')
      log.info('[asm.js] fallback to nasm')
      return fs.readFileSync('/tmp/poke_gen.bin')
    } catch (e2) {
      log.error(`[nasm fallback] ${e2.message}`)
      return null
    }
  }
}

// ── ARM64 assembly compile ──
async function compileAssemblyARM(asm) {
  const tmpAsm = '/tmp/poke_arm.s'
  const tmpObj = '/tmp/poke_arm.o'
  const tmpBin = '/tmp/poke_arm.bin'

  fs.writeFileSync(tmpAsm, asm)
  try {
    execSync(`aarch64-elf-as -o ${tmpObj} ${tmpAsm} 2>&1`)
    execSync(`aarch64-elf-objcopy -O binary ${tmpObj} ${tmpBin} 2>&1`)
    return fs.readFileSync(tmpBin)
  } catch (e) {
    log.error(`[arm-as] ${e.message}`)
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

module.exports = {
  compileAssembly,
  compileAssemblyARM,
  runImageCode,
}
