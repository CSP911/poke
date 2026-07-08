/**
 * POKE Library — unit tests (modular architecture)
 *
 * Tests device library loading, matching, tool generation.
 * Modular: chip base + sensor/actuator modules matched independently.
 *
 * Usage: node test/library.test.js
 */

const path = require('path')
const fs = require('fs')

process.env.LOG_LEVEL = 'error'

const {
  loadLibrary,
  matchEdge,
  generateLibraryTools,
  getLibrarySummary,
  entries,
} = require(path.join(__dirname, '..', 'hub', 'library'))

let pass = 0
let fail = 0
const results = []

function assert(condition, name) {
  if (condition) {
    pass++
    results.push(`  PASS  ${name}`)
  } else {
    fail++
    results.push(`  FAIL  ${name}`)
  }
}

function assertEq(actual, expected, name) {
  assert(actual === expected, `${name} (got ${actual}, expected ${expected})`)
}

async function runTests() {
  // === 1. Loading ===
  loadLibrary()

  assert(entries.size >= 5, `loaded at least 5 entries (got ${entries.size})`)
  assert(entries.has('esp32c3_base'), 'esp32c3_base exists')
  assert(entries.has('dht22'), 'dht22 exists')
  assert(entries.has('bmp280'), 'bmp280 exists')
  assert(entries.has('hcsr501'), 'hcsr501 exists')
  assert(entries.has('relay_2ch'), 'relay_2ch exists')

  // === 2. Chip base entry ===
  const base = entries.get('esp32c3_base')
  assertEq(base.arch, 'riscv32', 'base arch')
  assertEq(base.chip, 'esp32c3', 'base chip')
  assert(base.match?.chip === 'esp32c3', 'base match.chip')
  assert(base.operations.length >= 7, `base has >= 7 ops (got ${base.operations.length})`)

  const baseOps = base.operations.map(o => o.name)
  assert(baseOps.includes('read_temp'), 'base has read_temp')
  assert(baseOps.includes('read_gpio'), 'base has read_gpio')
  assert(baseOps.includes('set_gpio'), 'base has set_gpio')
  assert(baseOps.includes('execute_code'), 'base has execute_code')
  assert(baseOps.includes('monitor_gpio'), 'base has monitor_gpio')
  assert(baseOps.includes('monitor_temp'), 'base has monitor_temp')

  // === 3. Sensor modules — chip-independent ===
  const dht = entries.get('dht22')
  assertEq(dht.chip, null, 'dht22 chip is null (chip-independent)')
  assertEq(dht.arch, null, 'dht22 arch is null')
  assert(dht.match?.sensor === 'dht22', 'dht22 match.sensor')
  assert(dht.wiring, 'dht22 has wiring')
  assert(dht.operations.length >= 1, 'dht22 has operations')

  const bmp = entries.get('bmp280')
  assertEq(bmp.chip, null, 'bmp280 chip is null')
  assert(bmp.match?.sensor === 'bmp280', 'bmp280 match.sensor')
  assert(bmp.operations.length >= 2, 'bmp280 has >= 2 operations')

  const pir = entries.get('hcsr501')
  assert(pir.match?.sensor === 'hcsr501', 'hcsr501 match.sensor')
  assert(pir.operations.some(o => o.name === 'read_motion'), 'hcsr501 has read_motion')
  assert(pir.operations.some(o => o.name === 'monitor_motion'), 'hcsr501 has monitor_motion')

  const relay = entries.get('relay_2ch')
  assert(relay.match?.sensor === 'relay_2ch', 'relay match.sensor')
  assert(relay.operations.some(o => o.name === 'relay_on'), 'relay has relay_on')
  assert(relay.operations.some(o => o.name === 'relay_off'), 'relay has relay_off')

  // === 4. Matching — chip only ===
  {
    const matched = matchEdge({}, { chip: 'esp32c3' })
    assert(matched.length === 1, `chip-only match returns 1 (got ${matched.length})`)
    assertEq(matched[0].id, 'esp32c3_base', 'matched esp32c3_base')
  }

  // === 5. Matching — chip + sensors (modular composition) ===
  {
    const matched = matchEdge({}, { chip: 'esp32c3', sensors: ['dht22', 'hcsr501'] })
    assert(matched.length === 3, `chip + 2 sensors = 3 matches (got ${matched.length})`)
    const ids = matched.map(e => e.id)
    assert(ids.includes('esp32c3_base'), 'includes base')
    assert(ids.includes('dht22'), 'includes dht22')
    assert(ids.includes('hcsr501'), 'includes hcsr501')
  }

  // === 6. Matching — sensors only (no chip) ===
  {
    const matched = matchEdge({}, { sensors: ['bmp280', 'relay_2ch'] })
    assertEq(matched.length, 2, 'sensor-only match returns 2')
    const ids = matched.map(e => e.id)
    assert(ids.includes('bmp280'), 'includes bmp280')
    assert(ids.includes('relay_2ch'), 'includes relay_2ch')
  }

  // === 7. Matching — unknown chip, no match ===
  {
    const matched = matchEdge({}, { chip: 'stm32f401' })
    assertEq(matched.length, 0, 'unknown chip = no match')
  }

  // === 8. Tool generation — chip only ===
  {
    const matched = matchEdge({}, { chip: 'esp32c3' })
    const tools = generateLibraryTools('edge-1', matched)
    assert(tools.length >= 7, `base generates >= 7 tools (got ${tools.length})`)
    assert(tools.every(t => t.name.startsWith('esp32c3_base__')), 'all tools prefixed with esp32c3_base__')
  }

  // === 9. Tool generation — composite (chip + sensors) ===
  {
    const matched = matchEdge({}, { chip: 'esp32c3', sensors: ['dht22', 'relay_2ch'] })
    const tools = generateLibraryTools('edge-2', matched)
    const names = tools.map(t => t.name)
    assert(names.some(n => n.startsWith('esp32c3_base__')), 'has base tools')
    assert(names.some(n => n.startsWith('dht22__')), 'has dht22 tools')
    assert(names.some(n => n.startsWith('relay_2ch__')), 'has relay tools')
    assert(tools.length >= 10, `composite generates >= 10 tools (got ${tools.length})`)
  }

  // === 10. Same sensor on different edges ===
  {
    const matched1 = matchEdge({}, { chip: 'esp32c3', sensors: ['dht22'] })
    const matched2 = matchEdge({}, { chip: 'stm32f401', sensors: ['dht22'] })  // unknown chip
    const tools1 = generateLibraryTools('esp32-1', matched1)
    const tools2 = generateLibraryTools('stm32-1', matched2)

    // Both should have dht22 tools
    assert(tools1.some(t => t.name.startsWith('dht22__')), 'esp32 has dht22 tools')
    assert(tools2.some(t => t.name.startsWith('dht22__')), 'stm32 has dht22 tools (reuse)')
    // But only esp32 has base tools
    assert(tools1.some(t => t.name.startsWith('esp32c3_base__')), 'esp32 has base tools')
    assert(!tools2.some(t => t.name.startsWith('esp32c3_base__')), 'stm32 does NOT have esp32 base tools')
  }

  // === 11. Tool structure ===
  {
    const matched = matchEdge({}, { chip: 'esp32c3', sensors: ['hcsr501'] })
    const tools = generateLibraryTools('edge-3', matched)
    for (const t of tools) {
      assert(t.name, `tool has name: ${t.name}`)
      assert(t.description, `${t.name} has description`)
      assert(t.input_schema, `${t.name} has input_schema`)
      assert(t._library, `${t.name} has _library`)
      assert(t._op, `${t.name} has _op`)
    }
  }

  // === 12. Default target ===
  {
    const matched = matchEdge({}, { chip: 'esp32c3' })
    const tools = generateLibraryTools('my-esp', matched)
    assert(tools.every(t => t._defaultTarget === 'my-esp'), 'all tools have correct default target')
  }

  // === 13. Index structure ===
  {
    const indexPath = path.join(__dirname, '..', 'library', 'index.json')
    const index = JSON.parse(fs.readFileSync(indexPath, 'utf8'))
    assertEq(index._version, '2.0', 'index version is 2.0')
    assert(index.by_chip, 'index has by_chip')
    assert(index.by_sensor, 'index has by_sensor')
    assert(index.by_vendor_device, 'index has by_vendor_device')

    // Chip entries should point to base
    assert(index.by_chip.esp32c3.includes('esp32c3_base'), 'chip index points to base')
    // Sensor entries should point to modules
    assert(index.by_sensor.dht22.includes('dht22'), 'sensor index points to module')
    assert(index.by_sensor.hcsr501.includes('hcsr501'), 'sensor index points to module')
  }

  // === 14. Edge cases ===
  {
    assertEq(matchEdge({}, null).length, 0, 'null info = no match')
    assertEq(matchEdge({}, {}).length, 0, 'empty info = no match')
    assertEq(matchEdge(null, { chip: 'esp32c3' }).length, 1, 'null node still matches by chip')
    assertEq(generateLibraryTools('x', []).length, 0, 'empty match = no tools')
  }

  // === 15. Summary ===
  {
    const summary = getLibrarySummary()
    assert(summary.includes('esp32c3_base'), 'summary has base')
    assert(summary.includes('dht22'), 'summary has dht22')
    assert(summary.includes('riscv32'), 'summary has arch')
  }

  // === BCM2835 (Pi Zero W) Tests ===
  console.log('  --- BCM2835 / ARMv6 ---')

  // 16. bcm2835_base entry loaded
  {
    const entry = entries.get('bcm2835_base')
    assert(entry !== undefined, 'bcm2835_base entry loaded')
    assertEq(entry?.arch, 'armv6', 'bcm2835_base arch is armv6')
    assertEq(entry?.chip, 'bcm2835', 'bcm2835_base chip is bcm2835')
  }

  // 17. Match by chip=bcm2835
  {
    const matched = matchEdge({}, { chip: 'bcm2835' })
    assert(matched.length > 0, 'bcm2835 chip matches')
    assert(matched.some(e => e.id === 'bcm2835_base'), 'matched bcm2835_base by chip')
  }

  // 18. No match for unknown chip
  {
    const matched = matchEdge({}, { chip: 'stm32f4' })
    assertEq(matched.length, 0, 'unknown chip matches nothing')
  }

  // 19. bcm2835 has registers
  {
    const entry = entries.get('bcm2835_base')
    assert(entry?.registers, 'bcm2835_base has registers')
    assert(entry?.registers?.GPIO_BASE, 'bcm2835 has GPIO_BASE register')
    assertEq(entry?.registers?.GPIO_BASE?.addr, '0x20200000', 'GPIO_BASE address correct')
  }

  // 20. bcm2835 has operations
  {
    const entry = entries.get('bcm2835_base')
    assert(entry?.operations && entry.operations.length >= 3, 'bcm2835_base has >= 3 operations')
    const opNames = entry?.operations?.map(o => o.name) || []
    assert(opNames.includes('read_gpio'), 'has read_gpio operation')
    assert(opNames.includes('set_gpio'), 'has set_gpio operation')
    assert(opNames.includes('execute_code'), 'has execute_code operation')
  }

  // 21. Generate tools for bcm2835
  {
    const matched = matchEdge({}, { chip: 'bcm2835' })
    const tools = generateLibraryTools('pi0w-test', matched)
    assert(tools.length >= 3, 'generates >= 3 tools for bcm2835')
    const toolNames = tools.map(t => t.name)
    assert(toolNames.some(n => n.includes('read_gpio')), 'generated read_gpio tool')
    assert(toolNames.some(n => n.includes('set_gpio')), 'generated set_gpio tool')
  }

  // 22. bcm2835 + sensor combo match
  {
    const matched = matchEdge({}, { chip: 'bcm2835', sensors: ['dht22'] })
    assert(matched.some(e => e.id === 'bcm2835_base'), 'combo match includes bcm2835_base')
    assert(matched.some(e => e.id === 'dht22'), 'combo match includes dht22')
    assert(matched.length >= 2, 'combo match has >= 2 entries')
  }

  // 23. bcm2835 devices (wifi, camera)
  {
    const entry = entries.get('bcm2835_base')
    assert(entry?.devices?.wifi, 'bcm2835 has wifi device info')
    assertEq(entry?.devices?.wifi?.interface, 'SDIO', 'wifi interface is SDIO')
    assert(entry?.devices?.camera, 'bcm2835 has camera device info')
    assertEq(entry?.devices?.camera?.sensor, 'IMX219', 'camera sensor is IMX219')
  }

  // 24. bcm2835 summary includes key info
  {
    const summary = getLibrarySummary()
    assert(summary.includes('bcm2835'), 'summary includes bcm2835')
    assert(summary.includes('armv6'), 'summary includes armv6')
  }

  // ── Print ──
  console.log('')
  results.forEach(r => console.log(r))
  console.log('')
  console.log(`${pass} passed, ${fail} failed, ${pass + fail} total`)
  process.exit(fail > 0 ? 1 : 0)
}

runTests().catch(e => { console.error('Fatal:', e); process.exit(1) })
