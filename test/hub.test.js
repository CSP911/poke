/**
 * POKE Hub — endpoint unit tests
 *
 * Usage: node test/hub.test.js
 * No external dependencies.
 */

const http = require('http')
const path = require('path')

// Set up env before requiring hub modules
process.env.HUB_SECRET = 'test-secret-123'
process.env.LOG_LEVEL = 'error' // suppress logs during tests

const { createServer } = require(path.join(__dirname, '..', 'hub', 'server'))
const { nodes } = require(path.join(__dirname, '..', 'hub', 'nodes'))

let server
let port

function request(method, urlPath, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const options = {
      hostname: 'localhost',
      port,
      path: urlPath,
      method,
      headers: {
        'Content-Type': 'application/json',
        ...headers,
      },
    }

    const req = http.request(options, (res) => {
      let data = ''
      res.on('data', c => data += c)
      res.on('end', () => {
        let parsed = null
        try { parsed = JSON.parse(data) } catch (e) { /* not JSON */ }
        resolve({ status: res.statusCode, body: data, json: parsed })
      })
    })
    req.on('error', reject)
    if (body) req.write(typeof body === 'string' ? body : JSON.stringify(body))
    req.end()
  })
}

// ── Test runner ──
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

async function runTests() {
  // ── Auth rejection ──
  {
    const res = await request('POST', '/enroll', { node_id: 'test', endpoint: 'http://localhost:9999' })
    assert(res.status === 401, 'POST /enroll without auth returns 401')
    assert(res.json && res.json.error === 'unauthorized', 'POST /enroll without auth returns error body')
  }

  // ── Auth with token query param ──
  {
    const res = await request('POST', '/enroll?token=test-secret-123', {
      node_id: 'test-node-1',
      endpoint: 'http://localhost:9999',
      arch: 'i386',
      memory_mb: 512,
    })
    assert(res.status === 200, 'POST /enroll with token query param returns 200')
    assert(res.json && res.json.ok === true, 'POST /enroll returns ok: true')
    assert(res.json && res.json.enrolled === 'test-node-1', 'POST /enroll returns enrolled node_id')
  }

  // ── Auth with Bearer header ──
  {
    const res = await request('POST', '/enroll', {
      node_id: 'test-node-2',
      endpoint: 'http://localhost:9998',
      arch: 'aarch64',
      memory_mb: 256,
    }, { Authorization: 'Bearer test-secret-123' })
    assert(res.status === 200, 'POST /enroll with Bearer header returns 200')
    assert(res.json && res.json.enrolled === 'test-node-2', 'POST /enroll with Bearer enrolls correctly')
  }

  // ── Invalid enroll (missing fields) ──
  {
    const res = await request('POST', '/enroll?token=test-secret-123', { node_id: 'bad' })
    assert(res.status === 400, 'POST /enroll with missing endpoint returns 400')
    assert(res.json && res.json.error.includes('endpoint'), 'POST /enroll error mentions endpoint')
  }

  {
    const res = await request('POST', '/enroll?token=test-secret-123', { endpoint: 'http://x' })
    assert(res.status === 400, 'POST /enroll with missing node_id returns 400')
  }

  // ── Invalid JSON ──
  {
    const res = await request('POST', '/enroll?token=test-secret-123', '{not json}')
    assert(res.status === 400, 'POST /enroll with invalid JSON returns 400')
  }

  // ── GET /nodes ──
  {
    const res = await request('GET', '/nodes')
    assert(res.status === 200, 'GET /nodes returns 200')
    assert(res.json && Array.isArray(res.json.nodes), 'GET /nodes returns nodes array')
    assert(res.json.nodes.length >= 2, 'GET /nodes has enrolled nodes')

    const node1 = res.json.nodes.find(n => n.node_id === 'test-node-1')
    assert(node1 && node1.arch === 'i386', 'GET /nodes includes test-node-1 with correct arch')
    assert(node1 && node1.status === 'alive', 'Enrolled node has alive status')
  }

  // ── GET /nodes does not require auth ──
  {
    const res = await request('GET', '/nodes')
    assert(res.status === 200, 'GET /nodes does not require auth')
  }

  // ── 404 for unknown routes ──
  {
    const res = await request('GET', '/nonexistent')
    assert(res.status === 404, 'GET /nonexistent returns 404')
    assert(res.json && res.json.error === 'not found', 'Unknown route returns not found error')
  }

  // ── POST /run without auth ──
  {
    const res = await request('POST', '/run', { task: 'add 2 and 3' })
    assert(res.status === 401, 'POST /run without auth returns 401')
  }

  // ── POST /relay without auth ──
  {
    const res = await request('POST', '/relay', { from: 'x', command: 'test' })
    assert(res.status === 401, 'POST /relay without auth returns 401')
  }

  // ── POST /poke-raw invalid body ──
  {
    const res = await request('POST', '/poke-raw?token=test-secret-123', { no_hex: true })
    assert(res.status === 400, 'POST /poke-raw without hex returns 400')
  }

  // ── GET /dashboard ──
  {
    const res = await request('GET', '/dashboard')
    assert(res.status === 200, 'GET /dashboard returns 200')
    assert(res.json && res.json.edges, 'GET /dashboard returns edges object')
    assert(res.json && res.json.timestamp, 'GET /dashboard includes timestamp')
  }

  // ── GET /dashboard/:id ──
  {
    const res = await request('GET', '/dashboard/test-node-1')
    assert(res.status === 200, 'GET /dashboard/:id returns 200')
    assert(res.json && res.json.node_id === 'test-node-1', 'GET /dashboard/:id returns correct node_id')
    assert(res.json && Array.isArray(res.json.history), 'GET /dashboard/:id returns history array')
  }

  // ── POST /mobile/register ──
  {
    const res = await request('POST', '/mobile/register', { node_id: 'mobile-test-1' })
    assert(res.status === 200, 'POST /mobile/register returns 200')
    assert(res.json && res.json.ok === true, 'POST /mobile/register returns ok')
    assert(nodes.has('mobile-test-1'), 'Mobile node is registered in nodes map')
  }

  // ── GET /mobile/poll/:id (no pending) ──
  {
    const res = await request('GET', '/mobile/poll/mobile-test-1')
    assert(res.status === 200, 'GET /mobile/poll/:id returns 200')
    assert(res.json && res.json.pending === false, 'GET /mobile/poll with no pending returns false')
  }

  // ── POST /run with missing task ──
  {
    const res = await request('POST', '/run?token=test-secret-123', { no_task: true })
    assert(res.status === 400, 'POST /run without task returns 400')
  }

  // ── POST /draw with missing task ──
  {
    const res = await request('POST', '/draw?token=test-secret-123', { no_task: true })
    assert(res.status === 400, 'POST /draw without task returns 400')
  }
}

// ── Main ──
async function main() {
  server = createServer()

  await new Promise((resolve) => {
    server.listen(0, () => {
      port = server.address().port
      resolve()
    })
  })

  console.log(`POKE Hub endpoint tests (port ${port})\n`)

  try {
    await runTests()
  } catch (e) {
    console.error('Test runner error:', e)
    fail++
  }

  // Print results
  for (const r of results) console.log(r)
  console.log(`\n${pass} passed, ${fail} failed, ${pass + fail} total`)

  server.close()
  process.exit(fail > 0 ? 1 : 0)
}

main()
