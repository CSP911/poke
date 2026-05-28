/**
 * POKE 디바이스 프로빙 — 미지의 PCI 디바이스를 탐색하고 프로파일 생성
 * node probe.js [slot]
 */
const http = require('http')
const fs = require('fs')
const { execSync } = require('child_process')

const POKE_ENDPOINT = 'http://localhost:8080'
const PROFILE_DIR = __dirname + '/profiles'

// PCI Vendor/Device 데이터베이스 (주요 항목)
const PCI_VENDORS = {
  '8086': 'Intel',
  '1234': 'QEMU',
  '1AF4': 'Red Hat (VirtIO)',
  '10EC': 'Realtek',
  '14E4': 'Broadcom',
  '10DE': 'NVIDIA',
  '1002': 'AMD/ATI',
}

const PCI_DEVICES = {
  '8086:1237': { name: 'Intel 440FX Host Bridge', type: 'bridge' },
  '8086:7000': { name: 'Intel PIIX3 ISA Bridge', type: 'bridge' },
  '8086:100E': { name: 'Intel 82540EM (e1000)', type: 'network' },
  '8086:2415': { name: 'Intel AC97 Audio', type: 'audio' },
  '1234:1111': { name: 'QEMU stdvga', type: 'graphics' },
  '1AF4:1000': { name: 'VirtIO Network', type: 'network' },
  '1AF4:1001': { name: 'VirtIO Block', type: 'storage' },
  '1AF4:1002': { name: 'VirtIO Balloon', type: 'memory' },
  '1AF4:1003': { name: 'VirtIO Console', type: 'serial' },
  '1AF4:1004': { name: 'VirtIO SCSI', type: 'storage' },
  '1AF4:1005': { name: 'VirtIO RNG', type: 'rng' },
  '1AF4:1009': { name: 'VirtIO 9P', type: 'filesystem' },
  '1AF4:1050': { name: 'VirtIO GPU', type: 'graphics' },
  '1AF4:1059': { name: 'VirtIO Sound', type: 'audio' },
}

// 어셈블리 컴파일 + POKE 전송
function pokeExec(asmCode) {
  fs.writeFileSync('/tmp/probe.asm', asmCode)
  try {
    execSync('nasm -f bin -o /tmp/probe.bin /tmp/probe.asm 2>&1')
  } catch (e) {
    console.error('[nasm]', e.stdout?.toString() || e.message)
    return null
  }
  const bin = fs.readFileSync('/tmp/probe.bin')

  return new Promise((resolve) => {
    const req = http.request({
      hostname: 'localhost', port: 8080, path: '/poke', method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': bin.length },
      timeout: 5000,
    }, (res) => {
      let d = ''; res.on('data', c => d += c); res.on('end', () => resolve(d.trim()))
    })
    req.on('error', () => resolve(null))
    req.write(bin); req.end()
  })
}

// LLM에게 디바이스 제어 방법 질문
async function askLLM(question) {
  const Anthropic = require('@anthropic-ai/sdk')
  const client = new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY })

  const msg = await client.messages.create({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 1000,
    system: `You are a hardware expert. You generate x86 (i386, 32-bit) NASM assembly code for bare-metal PCI device interaction.

Rules:
- BITS 32
- Use in/out for port I/O (use dx for port, al/ax/eax for data)
- For MMIO: read/write memory directly
- Return result in EAX
- End with RET
- Output ONLY assembly code, no explanation
- Keep it simple and focused`,
    messages: [{ role: 'user', content: question }],
  })

  let text = msg.content[0].type === 'text' ? msg.content[0].text.trim() : ''
  return text.replace(/^```\w*\n?/, '').replace(/\n?```$/, '').trim()
}

// 1단계: PCI 슬롯 읽기
async function readPCISlot(slot) {
  const addr = 0x80000000 | (slot << 11)
  const result = await pokeExec(`BITS 32\nmov eax, ${addr}\nmov dx, 0xCF8\nout dx, eax\nmov dx, 0xCFC\nin eax, dx\nret`)
  if (!result) return null

  const match = result.match(/eax=(\d+)/)
  if (!match) return null
  const val = parseInt(match[1])
  if ((val & 0xFFFF) === 0xFFFF) return null

  const vendor = (val & 0xFFFF).toString(16).toUpperCase().padStart(4, '0')
  const device = ((val >> 16) & 0xFFFF).toString(16).toUpperCase().padStart(4, '0')
  return { slot, vendor, device, raw: val }
}

// PCI class/subclass 읽기
async function readPCIClass(slot) {
  const addr = 0x80000000 | (slot << 11) | 0x08
  const result = await pokeExec(`BITS 32\nmov eax, ${addr}\nmov dx, 0xCF8\nout dx, eax\nmov dx, 0xCFC\nin eax, dx\nret`)
  if (!result) return null
  const match = result.match(/eax=(\d+)/)
  if (!match) return null
  const val = parseInt(match[1])
  return {
    classCode: (val >> 24) & 0xFF,
    subclass: (val >> 16) & 0xFF,
    progif: (val >> 8) & 0xFF,
    revision: val & 0xFF,
  }
}

// BAR0 읽기
async function readBAR0(slot) {
  const addr = 0x80000000 | (slot << 11) | 0x10
  const result = await pokeExec(`BITS 32\nmov eax, ${addr}\nmov dx, 0xCF8\nout dx, eax\nmov dx, 0xCFC\nin eax, dx\nret`)
  if (!result) return null
  const match = result.match(/eax=(\d+)/)
  if (!match) return null
  const val = parseInt(match[1]) >>> 0
  const isIO = val & 1
  const addr32 = val & 0xFFFFFFF0
  return { raw: val, isIO, address: addr32 }
}

// 2단계: LLM으로 탐침 코드 생성
async function probeDevice(slot, devInfo, bar0) {
  const key = `${devInfo.vendor}:${devInfo.device}`
  const known = PCI_DEVICES[key]
  const vendorName = PCI_VENDORS[devInfo.vendor] || 'Unknown'

  console.log(`\n[probe] Slot ${slot}: ${vendorName} ${key}`)
  console.log(`[probe] ${known ? known.name + ' (' + known.type + ')' : 'UNKNOWN DEVICE'}`)
  console.log(`[probe] BAR0: 0x${bar0.address.toString(16).toUpperCase()} (${bar0.isIO ? 'I/O' : 'MMIO'})`)

  if (!known) {
    // 완전 미지 디바이스 → LLM에게 물어보기
    console.log(`[probe] Asking LLM about ${key}...`)
    const question = `PCI device vendor=${devInfo.vendor} device=${devInfo.device} (${vendorName}).
BAR0 is at 0x${bar0.address.toString(16)} (${bar0.isIO ? 'I/O port' : 'memory-mapped'}).
Write x86 assembly to read the first 4 bytes from BAR0 to identify this device.
${bar0.isIO ? 'Use in dx, eax instructions.' : 'Read from memory address directly.'}`

    const asm = await askLLM(question)
    console.log(`[probe] LLM generated:\n${asm}`)

    const result = await pokeExec(asm)
    console.log(`[probe] Result: ${result}`)

    return { slot, key, name: 'Unknown', bar0, probeResult: result, asm }
  }

  // 알려진 디바이스 → 타입별 탐침
  console.log(`[probe] Known device. Probing ${known.type}...`)

  let probeResult = null
  let probeAsm = null

  if (known.type === 'rng') {
    // VirtIO RNG: BAR0 MMIO에서 magic number 읽기
    const question = `VirtIO device at MMIO address 0x${bar0.address.toString(16)}.
Read the magic number at offset 0x00 (should be 0x74726976 = "virt" for VirtIO).
Also read device ID at offset 0x08.
Return magic in eax.`
    probeAsm = await askLLM(question)
    console.log(`[probe] ASM:\n${probeAsm}`)
    probeResult = await pokeExec(probeAsm)
    console.log(`[probe] Magic: ${probeResult}`)
  }

  else if (known.type === 'memory') {
    // VirtIO Balloon: 같은 방법
    const question = `VirtIO device at MMIO address 0x${bar0.address.toString(16)}.
Read the magic number at offset 0x00 and version at offset 0x04.
Return magic in eax.`
    probeAsm = await askLLM(question)
    probeResult = await pokeExec(probeAsm)
    console.log(`[probe] Result: ${probeResult}`)
  }

  else if (known.type === 'network') {
    // e1000: MAC 읽기
    const question = `Intel e1000 NIC at MMIO base 0x${bar0.address.toString(16)}.
Read the MAC address from RAL register at offset 0x5400.
Return low 32 bits of MAC in eax.`
    probeAsm = await askLLM(question)
    probeResult = await pokeExec(probeAsm)
    console.log(`[probe] MAC low: ${probeResult}`)
  }

  else if (known.type === 'graphics') {
    // VGA: read BochsVBE version
    probeAsm = `BITS 32\nmov dx, 0x01CE\nmov ax, 0\nout dx, ax\nmov dx, 0x01CF\nin ax, dx\nmovzx eax, ax\nret`
    probeResult = await pokeExec(probeAsm)
    console.log(`[probe] BochsVBE ID: ${probeResult}`)
  }

  return { slot, key, name: known.name, type: known.type, bar0, probeResult, probeAsm }
}

// 프로파일 저장
function saveProfile(result) {
  if (!fs.existsSync(PROFILE_DIR)) fs.mkdirSync(PROFILE_DIR)
  const filename = `${PROFILE_DIR}/${result.key.replace(':', '_')}.json`
  const profile = {
    vendor_device: result.key,
    name: result.name,
    type: result.type || 'unknown',
    bar0: result.bar0,
    probe_result: result.probeResult,
    probe_asm: result.probeAsm,
    discovered_at: new Date().toISOString(),
  }
  fs.writeFileSync(filename, JSON.stringify(profile, null, 2))
  console.log(`[profile] Saved: ${filename}`)
}

// 메인
async function main() {
  const targetSlot = process.argv[2] ? parseInt(process.argv[2]) : null

  console.log('=== POKE Device Probe ===\n')

  // PCI 전체 스캔
  console.log('[scan] Scanning PCI bus 0...')
  const devices = []
  for (let slot = 0; slot < 32; slot++) {
    const dev = await readPCISlot(slot)
    if (dev) devices.push(dev)
  }
  console.log(`[scan] Found ${devices.length} devices\n`)

  // 각 디바이스 정보 출력
  for (const dev of devices) {
    const key = `${dev.vendor}:${dev.device}`
    const known = PCI_DEVICES[key]
    const vendorName = PCI_VENDORS[dev.vendor] || '???'
    console.log(`  slot ${dev.slot}: ${vendorName} ${key} — ${known ? known.name : 'UNKNOWN'}`)
  }

  // 탐침 대상 선택
  const targets = targetSlot !== null
    ? devices.filter(d => d.slot === targetSlot)
    : devices

  console.log('')

  for (const dev of targets) {
    const cls = await readPCIClass(dev.slot)
    const bar0 = await readBAR0(dev.slot)
    if (!bar0) continue

    console.log(`  class=${cls?.classCode?.toString(16)} sub=${cls?.subclass?.toString(16)}`)

    const result = await probeDevice(dev.slot, dev, bar0)
    if (result) saveProfile(result)
  }

  console.log('\n=== Probe Complete ===')
  if (fs.existsSync(PROFILE_DIR)) {
    const files = fs.readdirSync(PROFILE_DIR).filter(f => f.endsWith('.json'))
    console.log(`Profiles saved: ${files.length}`)
    files.forEach(f => console.log(`  ${f}`))
  }
}

main().catch(console.error)
