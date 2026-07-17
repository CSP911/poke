#!/usr/bin/env node
/**
 * JARVIS — Launch 10 QEMU edges for smart office simulation
 *
 * Each edge runs a POKE kernel in QEMU with UART mapped to a TCP port.
 * The hub connects to each edge via serial://tcp:localhost:PORT
 *
 * Usage: node jarvis/launch.js [--hub]
 *   --hub    Also start the hub server (default: edges only)
 */

const { spawn, execSync } = require('child_process')
const fs = require('fs')
const path = require('path')
const net = require('net')

const ROOT = path.resolve(__dirname, '..')
const config = JSON.parse(fs.readFileSync(path.join(__dirname, 'config.json'), 'utf8'))

// Binary paths
const BINS = {
  rv32:  path.join(ROOT, 'rv32', 'poke-rv32.bin'),
  arm64: path.join(ROOT, 'arm', 'poke-arm.bin'),
  armv6: path.join(ROOT, 'pi0w', 'poke-pi0w-qemu.bin'),
}

// QEMU commands per arch
const QEMU_CMD = {
  rv32: (bin, port) => [
    'qemu-system-riscv32',
    '-machine', 'virt', '-cpu', 'rv32', '-m', '32M',
    '-kernel', bin, '-nographic', '-bios', 'none',
    '-chardev', `socket,id=uart0,host=127.0.0.1,port=${port},server=on,wait=off`,
    '-serial', 'chardev:uart0',
  ],
  arm64: (bin, port) => [
    'qemu-system-aarch64',
    '-machine', 'virt', '-cpu', 'cortex-a53', '-m', '32M',
    '-kernel', bin, '-display', 'none',
    '-chardev', 'null,id=con', '-serial', 'chardev:con',
    '-chardev', `socket,id=net,host=127.0.0.1,port=${port},server=on,wait=off`,
    '-serial', 'chardev:net',
    '-chardev', 'null,id=audio', '-serial', 'chardev:audio',
    '-monitor', 'none',
  ],
  armv6: (bin, port) => [
    'qemu-system-arm',
    '-M', 'virt', '-cpu', 'cortex-a7', '-m', '32M',
    '-kernel', bin, '-display', 'none',
    '-chardev', `socket,id=uart0,host=0.0.0.0,port=${port},server=on,wait=off`,
    '-serial', 'chardev:uart0',
    '-monitor', 'none',
  ],
}

// Build missing binaries
function ensureBinaries() {
  const builds = {
    rv32:  { dir: path.join(ROOT, 'rv32'), target: '' },
    arm64: { dir: path.join(ROOT, 'arm'), target: '' },
    armv6: { dir: path.join(ROOT, 'pi0w'), target: 'qemu' },
  }
  for (const [arch, bin] of Object.entries(BINS)) {
    if (!fs.existsSync(bin)) {
      console.log(`  BUILD  ${arch} kernel...`)
      try {
        execSync(`make -C ${builds[arch].dir} ${builds[arch].target}`, { stdio: 'pipe' })
      } catch (e) {
        console.error(`  ERROR  Failed to build ${arch}: ${e.message}`)
        process.exit(1)
      }
    }
  }
}

// Wait for TCP port to accept connections
function waitForPort(port, timeoutMs) {
  return new Promise((resolve, reject) => {
    const deadline = setTimeout(() => reject(new Error(`Port ${port} timeout`)), timeoutMs)
    const tryConnect = () => {
      const sock = new net.Socket()
      sock.on('error', () => { sock.destroy(); setTimeout(tryConnect, 100) })
      sock.connect(port, '127.0.0.1', () => {
        clearTimeout(deadline)
        sock.destroy()
        resolve()
      })
    }
    tryConnect()
  })
}

// Send POKE frame and get response
function pokePing(port) {
  return new Promise((resolve, reject) => {
    const sock = new net.Socket()
    const timeout = setTimeout(() => { sock.destroy(); reject(new Error('timeout')) }, 5000)
    let buf = Buffer.alloc(0)

    sock.connect(port, '127.0.0.1', () => {
      // Send PING
      const frame = Buffer.alloc(12)
      frame.write('POKE', 0)
      frame.writeUInt32LE(4, 4)
      frame.write('PING', 8)
      sock.write(frame)
    })

    sock.on('data', d => {
      buf = Buffer.concat([buf, d])
      // Look for RESP
      for (let i = 0; i < buf.length - 8; i++) {
        if (buf[i] === 0x52 && buf[i+1] === 0x45 && buf[i+2] === 0x53 && buf[i+3] === 0x50) {
          const len = buf.readUInt32LE(i + 4)
          if (i + 8 + len <= buf.length) {
            clearTimeout(timeout)
            sock.destroy()
            resolve(buf.slice(i + 8, i + 8 + len).toString())
            return
          }
        }
      }
    })

    sock.on('error', e => { clearTimeout(timeout); reject(e) })
  })
}

// ── Main ──
const processes = []

async function launch() {
  console.log('\n  ╔═══════════════════════════════════════╗')
  console.log('  ║   JARVIS — Smart Office Simulation    ║')
  console.log('  ║   10 QEMU Edges × 3 Architectures    ║')
  console.log('  ╚═══════════════════════════════════════╝\n')

  // Build kernels if needed
  ensureBinaries()

  // Launch all edges
  console.log('  Launching edges...\n')

  for (const edge of config.edges) {
    const bin = BINS[edge.arch]
    const args = QEMU_CMD[edge.arch](bin, edge.port)

    const proc = spawn(args[0], args.slice(1), {
      stdio: ['pipe', 'pipe', 'pipe'],
    })

    proc.on('error', e => console.error(`  [${edge.id}] spawn error: ${e.message}`))
    proc.stderr.on('data', () => {}) // absorb

    processes.push({ edge, proc })
  }

  // Wait for all edges to be ready
  console.log('  Waiting for edges to boot...\n')
  await new Promise(r => setTimeout(r, 3000))

  // Verify each edge
  let alive = 0
  for (const { edge } of processes) {
    try {
      await waitForPort(edge.port, 5000)
      const resp = await pokePing(edge.port)
      if (resp === 'PONG') {
        alive++
        const sensors = edge.sensors.join(', ')
        console.log(`  ✓ ${edge.id.padEnd(14)} ${edge.arch.padEnd(6)} :${edge.port}  [${sensors}]`)
      } else {
        console.log(`  ✗ ${edge.id.padEnd(14)} unexpected: ${resp}`)
      }
    } catch (e) {
      console.log(`  ✗ ${edge.id.padEnd(14)} ${e.message}`)
    }
  }

  console.log(`\n  ${alive}/${config.edges.length} edges alive\n`)

  if (alive === 0) {
    console.log('  No edges alive. Shutting down.')
    shutdown()
    process.exit(1)
  }

  console.log('  JARVIS running. Press Ctrl+C to stop.\n')
  console.log('  Edge ports:')
  for (const edge of config.edges) {
    console.log(`    ${edge.id}: tcp://127.0.0.1:${edge.port}`)
  }
  console.log('')
}

function shutdown() {
  console.log('\n  Shutting down JARVIS...')
  for (const { edge, proc } of processes) {
    proc.kill('SIGKILL')
  }
  console.log(`  ${processes.length} edges terminated.\n`)
}

process.on('SIGINT', () => { shutdown(); process.exit(0) })
process.on('SIGTERM', () => { shutdown(); process.exit(0) })

launch().catch(e => {
  console.error('Fatal:', e.message)
  shutdown()
  process.exit(1)
})
