/* Resident drivers — the library side of the 3-layer model:
 *   platform (kernel8.img) / resident drivers (edge/library, injected) / personas.
 * The kernel owns no device stacks; when a persona needs a service (touch, ...)
 * the hub uploads the resident that provides it first. */
const fs = require('fs')
const path = require('path')
const { execSync } = require('child_process')
const { upload } = require('./proto')

const LIB_DIR = path.join(__dirname, '..', '..', 'edge', 'library')

/* All residents in the library: [{name, arch, provides, dir, binary, ...device.json}] */
function list(arch = 'pi4') {
  const dir = path.join(LIB_DIR, arch)
  if (!fs.existsSync(dir)) return []
  return fs.readdirSync(dir)
    .map((d) => path.join(dir, d, 'device.json'))
    .filter((f) => fs.existsSync(f))
    .map((f) => ({ ...JSON.parse(fs.readFileSync(f, 'utf8')), dir: path.dirname(f) }))
    .filter((e) => e.kind === 'resident')
}

function byName(name, arch) { return list(arch).find((e) => e.name === name) }
function providing(service, arch) { return list(arch).find((e) => (e.provides || []).includes(service)) }

/* Which services a persona uses — from its POKE-META "services", else from the source. */
function servicesUsed(meta, source) {
  const s = new Set((meta && meta.services) || [])
  if (source && /->touch\s*\(/.test(source)) s.add('touch')
  return [...s]
}

async function currentResident(request) {
  try { return JSON.parse(await request(Buffer.from('INFO'))).resident || null } catch { return null }
}

/* Upload a resident by name (no-op if it is already the one running). */
async function ensureResident(request, name, arch = 'pi4') {
  const e = byName(name, arch)
  if (!e) throw new Error(`no resident "${name}" in ${LIB_DIR}/${arch}`)
  if (await currentResident(request) === name) return { resident: name, size: 0, already: true }
  const binPath = path.join(e.dir, e.binary || 'resident.bin')
  if (!fs.existsSync(binPath)) execSync('make', { cwd: e.dir, stdio: 'ignore' })   /* build artifact, not in git */
  const bin = fs.readFileSync(binPath)
  const r = JSON.parse(await upload(request, 'RSLD', bin, 20000))
  if (r.error) throw new Error(`resident ${name}: ${r.error}`)
  return r
}

/* Make sure every service the persona needs is provided before it is injected. */
async function ensureServices(request, services, arch = 'pi4') {
  for (const svc of services) {
    const e = providing(svc, arch)
    if (!e) { console.log(`  (no resident in the library provides "${svc}" — persona will see it as absent)`); continue }
    const r = await ensureResident(request, e.name, arch)
    if (!r.already) console.log(`  ✦ resident ${e.name} loaded (${r.size} bytes) — provides ${svc}`)
  }
}

async function stopResident(request) { return request(Buffer.from('RSTP')) }

module.exports = { LIB_DIR, list, byName, providing, servicesUsed, ensureResident, ensureServices, stopResident, currentResident }
