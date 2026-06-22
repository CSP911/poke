/**
 * POKE Library — unit tests
 *
 * Tests device library loading, matching, tool generation, and incubation.
 * No external hardware required.
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

// ── Tests ──

async function runTests() {
  // === 1. Library loading ===
  loadLibrary()

  assert(entries.size >= 2, 'loadLibrary loads at least 2 entries')
  assert(entries.has('esp32c3_internal'), 'esp32c3_internal entry exists')
  assert(entries.has('dht22_temp_humidity'), 'dht22_temp_humidity entry exists')

  // === 2. Entry structure validation ===
  const esp32 = entries.get('esp32c3_internal')
  assertEq(esp32.arch, 'riscv32', 'esp32c3 arch is riscv32')
  assertEq(esp32.chip, 'esp32c3', 'esp32c3 chip field')
  assert(esp32.match?.chip === 'esp32c3', 'esp32c3 match.chip')
  assert(Array.isArray(esp32.operations), 'esp32c3 has operations array')
  assert(esp32.operations.length >= 5, 'esp32c3 has at least 5 operations')

  // Check required operation fields
  for (const op of esp32.operations) {
    assert(op.name, `operation has name: ${op.name}`)
    assert(op.type, `operation ${op.name} has type`)
    assert(op.protocol, `operation ${op.name} has protocol`)
    assert(Array.isArray(op.tags), `operation ${op.name} has tags`)
  }

  // Check specific operations exist
  const opNames = esp32.operations.map(o => o.name)
  assert(opNames.includes('read_temp'), 'has read_temp operation')
  assert(opNames.includes('read_gpio'), 'has read_gpio operation')
  assert(opNames.includes('set_gpio'), 'has set_gpio operation')
  assert(opNames.includes('monitor_gpio'), 'has monitor_gpio operation')
  assert(opNames.includes('monitor_temp'), 'has monitor_temp operation')
  assert(opNames.includes('stop_monitors'), 'has stop_monitors operation')
  assert(opNames.includes('execute_code'), 'has execute_code operation')

  // === 3. DHT22 entry ===
  const dht = entries.get('dht22_temp_humidity')
  assertEq(dht.arch, 'riscv32', 'dht22 arch is riscv32')
  assert(dht.match?.sensor === 'dht22', 'dht22 match.sensor')
  assert(dht.wiring, 'dht22 has wiring info')

  // === 4. Matching — by chip ===
  {
    const node = { endpoint: 'serial:///dev/ttyUSB0' }
    const info = { chip: 'esp32c3', status: 'alive' }
    const matched = matchEdge(node, info)
    assert(matched.length >= 1, 'matchEdge by chip returns at least 1 entry')
    assert(matched.some(e => e.id === 'esp32c3_internal'), 'matched esp32c3_internal by chip')
  }

  // === 5. Matching — by sensor ===
  {
    const node = { endpoint: 'serial:///dev/ttyUSB0' }
    const info = { sensors: ['dht22'] }
    const matched = matchEdge(node, info)
    assert(matched.length >= 1, 'matchEdge by sensor returns at least 1 entry')
    assert(matched.some(e => e.id === 'dht22_temp_humidity'), 'matched dht22 by sensor')
  }

  // === 6. Matching — no match ===
  {
    const node = { endpoint: 'http://localhost:8080' }
    const info = { chip: 'stm32f401', status: 'alive' }
    const matched = matchEdge(node, info)
    assertEq(matched.length, 0, 'no match for unknown chip')
  }

  // === 7. Matching — multiple matches ===
  {
    const node = { endpoint: 'serial:///dev/ttyUSB0' }
    const info = { chip: 'esp32c3', sensors: ['temp_internal', 'dht22'] }
    const matched = matchEdge(node, info)
    assert(matched.length >= 2, 'multiple matches for chip + sensors')
  }

  // === 8. Tool generation ===
  {
    const node = { endpoint: 'serial:///dev/ttyUSB0' }
    const info = { chip: 'esp32c3' }
    const matched = matchEdge(node, info)
    const tools = generateLibraryTools('test-esp32', matched)
    assert(tools.length >= 5, `generates at least 5 tools (got ${tools.length})`)

    // Check tool structure
    for (const t of tools) {
      assert(t.name, `tool has name: ${t.name}`)
      assert(t.description, `tool ${t.name} has description`)
      assert(t.input_schema, `tool ${t.name} has input_schema`)
      assert(t._library, `tool ${t.name} has _library ref`)
      assert(t._op, `tool ${t.name} has _op ref`)
      assert(t._arch, `tool ${t.name} has _arch`)
    }

    // Check naming convention: {entry_id}__{op_name}
    const tempTool = tools.find(t => t.name === 'esp32c3_internal__read_temp')
    assert(tempTool, 'tool esp32c3_internal__read_temp exists')
    assert(tempTool._op.command === 'TEMP', 'read_temp uses TEMP command')
    assert(tempTool._defaultTarget === 'test-esp32', 'default target is node ID')
  }

  // === 9. Tool generation — actuator with params ===
  {
    const matched = matchEdge({}, { chip: 'esp32c3' })
    const tools = generateLibraryTools('esp32', matched)
    const gpioTool = tools.find(t => t.name === 'esp32c3_internal__set_gpio')
    assert(gpioTool, 'set_gpio tool exists')
    assert(gpioTool.input_schema.properties.pin, 'set_gpio has pin param')
    assert(gpioTool.input_schema.properties.value, 'set_gpio has value param')
  }

  // === 10. Tool generation — monitor with params ===
  {
    const matched = matchEdge({}, { chip: 'esp32c3' })
    const tools = generateLibraryTools('esp32', matched)
    const monTool = tools.find(t => t.name === 'esp32c3_internal__monitor_temp')
    assert(monTool, 'monitor_temp tool exists')
    assert(monTool.input_schema.properties.threshold, 'monitor_temp has threshold param')
    assert(monTool.input_schema.properties.interval_ms, 'monitor_temp has interval_ms param')
  }

  // === 11. Library summary ===
  {
    const summary = getLibrarySummary()
    assert(summary.length > 0, 'getLibrarySummary returns non-empty string')
    assert(summary.includes('esp32c3_internal'), 'summary includes esp32c3_internal')
    assert(summary.includes('riscv32'), 'summary includes architecture')
  }

  // === 12. Index structure ===
  {
    const indexPath = path.join(__dirname, '..', 'library', 'index.json')
    const index = JSON.parse(fs.readFileSync(indexPath, 'utf8'))
    assert(index.by_chip, 'index has by_chip')
    assert(index.by_sensor, 'index has by_sensor')
    assert(index.by_vendor_device, 'index has by_vendor_device')
    assert(index.by_chip.esp32c3, 'index.by_chip has esp32c3')
    assert(index.by_sensor.dht22, 'index.by_sensor has dht22')
    assert(index.by_vendor_device['8086:100E'], 'index.by_vendor_device has 8086:100E')
  }

  // === 13. Empty/null edge matching ===
  {
    assertEq(matchEdge({}, null).length, 0, 'matchEdge with null info returns empty')
    assertEq(matchEdge({}, {}).length, 0, 'matchEdge with empty info returns empty')
    assertEq(matchEdge(null, { chip: 'esp32c3' }).length >= 1, true, 'matchEdge with null node still matches by chip')
  }

  // === 14. Tool generation — empty match ===
  {
    const tools = generateLibraryTools('nobody', [])
    assertEq(tools.length, 0, 'generateLibraryTools with empty match returns empty')
  }

  // === 15. Protocol types ===
  {
    const matched = matchEdge({}, { chip: 'esp32c3' })
    const tools = generateLibraryTools('esp32', matched)
    const commandTools = tools.filter(t => t._op.protocol === 'command')
    assert(commandTools.length >= 5, 'at least 5 command-protocol tools')

    const dhtMatched = matchEdge({}, { sensors: ['dht22'] })
    const dhtTools = generateLibraryTools('sensor-node', dhtMatched)
    const execTools = dhtTools.filter(t => t._op.protocol === 'exec')
    assert(execTools.length >= 1, 'dht22 has at least 1 exec-protocol tool')
  }

  // ── Print results ──
  console.log('')
  results.forEach(r => console.log(r))
  console.log('')
  console.log(`${pass} passed, ${fail} failed, ${pass + fail} total`)

  process.exit(fail > 0 ? 1 : 0)
}

runTests().catch(e => { console.error('Fatal:', e); process.exit(1) })
