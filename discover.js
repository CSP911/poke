/**
 * POKE 디바이스 자동 탐색 — x86 + ARM 엣지에 탐색 코드 주입
 * node discover.js [node_id]
 */
const http = require('http')
const fs = require('fs')
const { execSync } = require('child_process')

const HUB = 'http://localhost:3333'

// x86 탐색 어셈블리: CPUID + PCI 스캔
const X86_DISCOVER_ASM = `
BITS 32

; result buffer in EDI (1st arg), result_len ptr in ESI (2nd arg)
push edi
push esi
mov edi, [esp+12]   ; result buf ptr
mov esi, [esp+16]   ; result len ptr

; === CPUID: vendor string ===
xor eax, eax
cpuid
; EBX-EDX-ECX = vendor
mov [edi], ebx
mov [edi+4], edx
mov [edi+8], ecx
mov byte [edi+12], ','
add edi, 13

; === CPUID: model info ===
mov eax, 1
cpuid
; EAX = version info
push eax
; Family
shr eax, 8
and eax, 0xF
add al, '0'
mov [edi], al
mov byte [edi+1], '.'
; Model
pop eax
shr eax, 4
and eax, 0xF
add al, '0'
mov [edi+2], al
mov byte [edi+3], ','
add edi, 4

; === PCI scan: first 8 slots on bus 0 ===
mov byte [edi], 'P'
mov byte [edi+1], 'C'
mov byte [edi+2], 'I'
mov byte [edi+3], ':'
add edi, 4

xor ecx, ecx        ; slot counter
.pci_loop:
cmp ecx, 32
jge .pci_done

; PCI CONFIG_ADDRESS: bus=0, slot=ecx, func=0, offset=0
mov eax, 0x80000000
mov ebx, ecx
shl ebx, 11
or eax, ebx
mov dx, 0xCF8
out dx, eax
mov dx, 0xCFC
in eax, dx

; Check if device exists (vendor != 0xFFFF)
cmp ax, 0xFFFF
je .pci_next

; Found device — write vendor:device as hex
push ecx
push eax

; Vendor (lower 16 bits)
movzx eax, ax
; Write hex
push eax
mov ecx, 4
.vh:
rol eax, 4
push eax
and al, 0xF
cmp al, 10
jb .vd
add al, 7
.vd:
add al, '0'
mov [edi], al
inc edi
pop eax
loop .vh
pop eax

mov byte [edi], ':'
inc edi

; Device (upper 16 bits)
pop eax
shr eax, 16
push eax
mov ecx, 4
.dh:
rol eax, 4
push eax
and al, 0xF
cmp al, 10
jb .dd
add al, 7
.dd:
add al, '0'
mov [edi], al
inc edi
pop eax
loop .dh
pop eax

mov byte [edi], ' '
inc edi

pop ecx

.pci_next:
inc ecx
jmp .pci_loop

.pci_done:
mov byte [edi], 0

; Calculate length
pop esi
pop edi            ; restore original edi (buf start)

; We need to recalc — just set len to ~200
push esi
mov esi, [esp+8]   ; result_len ptr
mov dword [esi], 200
pop esi

mov eax, 0
ret
`

// ARM64 탐색은 시리얼 프로토콜로
const ARM_DISCOVER = Buffer.from([
  0x50, 0x4F, 0x4B, 0x45,  // "POKE"
  0x04, 0x00, 0x00, 0x00,  // len=4
  0x49, 0x4E, 0x46, 0x4F   // "INFO"
])

async function discoverX86(nodeId) {
  console.log(`[discover] x86 node: ${nodeId}`)

  // Compile ASM
  fs.writeFileSync('/tmp/discover.asm', X86_DISCOVER_ASM)
  try {
    execSync('nasm -f bin -o /tmp/discover.bin /tmp/discover.asm 2>&1')
  } catch (e) {
    console.error('nasm error:', e.message)
    // Fallback: simple CPUID only
    const simple = `BITS 32
xor eax, eax
cpuid
ret`
    fs.writeFileSync('/tmp/discover.asm', simple)
    execSync('nasm -f bin -o /tmp/discover.bin /tmp/discover.asm')
  }

  const code = fs.readFileSync('/tmp/discover.bin')
  console.log(`[discover] compiled: ${code.length} bytes`)

  // Send to node
  const nodes = await getNodes()
  const node = nodes.find(n => n.node_id === nodeId)
  if (!node) { console.log('node not found'); return }

  return new Promise((resolve) => {
    const req = http.request({
      hostname: 'localhost', port: parseInt(new URL(node.endpoint).port),
      path: '/poke', method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': code.length },
      timeout: 5000,
    }, (res) => {
      let data = ''; res.on('data', c => data += c)
      res.on('end', () => {
        console.log(`[discover] result:`, data)
        resolve(data)
      })
    })
    req.on('error', e => { console.error(e.message); resolve(null) })
    req.write(code); req.end()
  })
}

async function discoverARM(nodeId) {
  console.log(`[discover] ARM node: ${nodeId} via TCP:8081`)

  return new Promise((resolve) => {
    const net = require('net')
    const sock = net.createConnection(8081, 'localhost', () => {
      sock.write(ARM_DISCOVER)
    })
    let data = Buffer.alloc(0)
    sock.on('data', (chunk) => {
      data = Buffer.concat([data, chunk])
      // Check for RESP header
      if (data.length >= 8 && data.slice(0, 4).toString() === 'RESP') {
        const len = data.readUInt32LE(4)
        if (data.length >= 8 + len) {
          const payload = data.slice(8, 8 + len).toString()
          console.log(`[discover] ARM result:`, payload)
          sock.end()
          resolve(payload)
        }
      }
    })
    sock.on('error', e => { console.error(e.message); resolve(null) })
    setTimeout(() => { sock.destroy(); resolve(null) }, 5000)
  })
}

function getNodes() {
  return new Promise((resolve) => {
    http.get(HUB + '/nodes', (res) => {
      let d = ''; res.on('data', c => d += c)
      res.on('end', () => {
        try { resolve(JSON.parse(d).nodes) }
        catch { resolve([]) }
      })
    }).on('error', () => resolve([]))
  })
}

async function main() {
  const nodeId = process.argv[2]

  if (!nodeId) {
    // Discover all
    const nodes = await getNodes()
    console.log(`Found ${nodes.length} nodes:`)
    for (const n of nodes) {
      console.log(`  ${n.node_id} (${n.arch}) @ ${n.endpoint}`)
      if (n.arch === 'i386' || n.arch === 'x86') {
        await discoverX86(n.node_id)
      } else if (n.arch === 'aarch64' || n.arch === 'arm' || n.arch === 'mobile') {
        await discoverARM(n.node_id)
      }
    }
  } else {
    const nodes = await getNodes()
    const node = nodes.find(n => n.node_id === nodeId)
    if (!node) { console.log('node not found'); return }
    if (node.arch === 'i386') await discoverX86(nodeId)
    else await discoverARM(nodeId)
  }
}

main().catch(console.error)
