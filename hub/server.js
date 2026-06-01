/**
 * POKE Hub — HTTP routing, auth, helpers
 */

const http = require('http')
const { log } = require('./logger')
const { nodes, edgeHistory, enrollNode, profiles, setMonitorTriggerCallback } = require('./nodes')
const { agentLoop } = require('./agent')
const { generateAssembly, generateImageCode, planCommand } = require('./llm')
const { compileAssembly } = require('./compiler')
const { pokeNode, pokeNodeRaw } = require('./transport')
const memory = require('./memory')

// ── Security: Auth + Helpers ──
const HUB_SECRET = process.env.HUB_SECRET || null

function checkAuth(req, res) {
  if (!HUB_SECRET) return true  // no secret = open mode (dev)
  const token = req.headers.authorization?.replace('Bearer ', '') ||
                new URL(req.url, `http://${req.headers.host}`).searchParams.get('token')
  if (token === HUB_SECRET) return true
  res.statusCode = 401
  res.end(JSON.stringify({ error: 'unauthorized' }))
  return false
}

function safeJSON(str) {
  try { return JSON.parse(str) }
  catch (e) {
    log.debug('JSON parse failed:', e.message)
    return null
  }
}

function jsonError(res, status, msg) {
  res.statusCode = status
  res.end(JSON.stringify({ error: msg }))
}

// ── Request body reader with bounded buffer ──
function readBody(req, maxBytes = 10 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    let body = ''
    let bytes = 0
    req.on('data', (chunk) => {
      bytes += chunk.length
      if (bytes > maxBytes) {
        req.destroy()
        reject(new Error('request body too large'))
        return
      }
      body += chunk
    })
    req.on('end', () => resolve(body))
    req.on('error', reject)
  })
}

// ── Main request handler ──
async function handleRequest(req, res) {
  const url = new URL(req.url, `http://${req.headers.host}`)
  let body
  try {
    body = await readBody(req)
  } catch (err) {
    jsonError(res, 413, err.message)
    return
  }

  // CORS
  res.setHeader('Content-Type', 'application/json')

  // POST /enroll — register node
  if (req.method === 'POST' && url.pathname === '/enroll') {
    if (!checkAuth(req, res)) return
    const node = safeJSON(body)
    if (!node || !node.node_id || !node.endpoint) { jsonError(res, 400, 'invalid body: need node_id, endpoint'); return }
    enrollNode(node)
    res.end(JSON.stringify({ ok: true, enrolled: node.node_id }))
    return
  }

  // GET /nodes — list nodes
  if (req.method === 'GET' && url.pathname === '/nodes') {
    res.end(JSON.stringify({ nodes: [...nodes.values()] }, null, 2))
    return
  }

  // POST /run — natural language -> LLM -> code -> execute
  if (req.method === 'POST' && url.pathname === '/run') {
    if (!checkAuth(req, res)) return
    const data = safeJSON(body)
    if (!data || !data.task) { jsonError(res, 400, 'invalid body: need task'); return }
    const { task, node_id } = data

    let target = node_id ? nodes.get(node_id) : [...nodes.values()].find(n => n.status === 'alive')
    if (!target) {
      jsonError(res, 404, 'no available node')
      return
    }

    log.info(`[run] task="${task}" -> ${target.node_id}`)

    const asm = await generateAssembly(task)
    if (!asm) {
      jsonError(res, 502, 'LLM failed to generate code')
      return
    }
    log.debug(`[asm]\n${asm}`)

    const machineCode = await compileAssembly(asm)
    if (!machineCode) {
      jsonError(res, 500, 'nasm compilation failed')
      return
    }
    log.info(`[compiled] ${machineCode.length} bytes`)

    const result = await pokeNode(target.endpoint, machineCode)
    log.info(`[result] ${result}`)

    res.end(JSON.stringify({
      task,
      node: target.node_id,
      asm,
      bytes: machineCode.length,
      result
    }, null, 2))
    return
  }

  // POST /draw — natural language -> image code -> execute -> send to POKE
  if (req.method === 'POST' && url.pathname === '/draw') {
    if (!checkAuth(req, res)) return
    const data = safeJSON(body)
    if (!data || !data.task) { jsonError(res, 400, 'invalid body: need task'); return }
    const { task, node_id } = data
    let target = node_id ? nodes.get(node_id) : [...nodes.values()].find(n => n.status === 'alive')
    if (!target) { jsonError(res, 404, 'no available node'); return }

    log.info(`[draw] task="${task}" -> ${target.node_id}`)

    const { runImageCode } = require('./compiler')
    const code = await generateImageCode(task)
    if (!code) { jsonError(res, 502, 'LLM failed'); return }
    log.info(`[draw] code generated (${code.length} chars)`)

    const imgResult = runImageCode(code)
    if (!imgResult) { jsonError(res, 500, 'code execution failed'); return }
    log.info(`[draw] image: ${imgResult.width}x${imgResult.height}, ${imgResult.body.length} bytes`)

    const pokeResult = await pokeNodeRaw(target.endpoint, imgResult.body)
    log.info(`[draw] poke result: ${pokeResult}`)

    res.end(JSON.stringify({
      task,
      node: target.node_id,
      width: imgResult.width,
      height: imgResult.height,
      bytes: imgResult.body.length,
      result: pokeResult,
      code_preview: code.slice(0, 200) + '...',
    }, null, 2))
    return
  }

  // POST /relay — agent loop (observe -> think -> act -> check -> loop)
  if (req.method === 'POST' && url.pathname === '/relay') {
    if (!checkAuth(req, res)) return
    const data = safeJSON(body)
    if (!data || !data.from || !data.command) { jsonError(res, 400, 'invalid body: need from, command'); return }
    const { from, command, target } = data
    log.info(`[agent] from=${from} -> "${command}"`)

    const fromNode = nodes.get(from)
    if (!fromNode) { jsonError(res, 404, 'unknown sender'); return }

    try {
      const result = await agentLoop(command, from, target)
      res.end(JSON.stringify({ from, command, ...result }, null, 2))
    } catch (err) {
      log.error(`[agent] ERROR: ${err.message}`)
      jsonError(res, 500, err.message)
    }
    return
  }

  // POST /poke-raw — binary direct send (debug)
  if (req.method === 'POST' && url.pathname === '/poke-raw') {
    if (!checkAuth(req, res)) return
    const data = safeJSON(body)
    if (!data || !data.hex) { jsonError(res, 400, 'invalid body: need hex'); return }
    const { node_id, hex } = data
    let target = node_id ? nodes.get(node_id) : [...nodes.values()][0]
    if (!target) { jsonError(res, 404, 'no node'); return }

    const buf = Buffer.from(hex, 'hex')
    const result = await pokeNode(target.endpoint, buf)
    res.end(JSON.stringify({ node: target.node_id, result }))
    return
  }

  // POST /event — edge fires autonomous event → re-enter agentLoop
  if (req.method === 'POST' && url.pathname === '/event') {
    const data = safeJSON(body)
    if (!data) { jsonError(res, 400, 'invalid event data'); return }

    log.info(`[event] from ${data.edge}: value=${data.value}, trigger=${data.trigger}`)

    const command = `EDGE EVENT from ${data.edge}: ` +
      `value=${data.value} triggered condition "${data.trigger}". ` +
      `Monitor ID: ${data.monitor}. ` +
      `Respond appropriately — alert user and/or take corrective action on other edges.`

    // Non-blocking: fire agentLoop and respond immediately
    agentLoop(command, data.edge, null).then(result => {
      log.info(`[event] handled: ${result.result?.slice(0, 80)}`)
    }).catch(err => {
      log.warn(`[event] handler failed: ${err.message}`)
    })

    res.end(JSON.stringify({ ok: true, received: true }))
    return
  }

  // GET /memory — get memory stats and recent facts
  if (req.method === 'GET' && url.pathname === '/memory') {
    const stats = memory.getStats()
    const facts = memory.loadFacts().slice(-20)
    res.end(JSON.stringify({ stats, facts }, null, 2))
    return
  }

  // GET /memory/history — search conversation history
  if (req.method === 'GET' && url.pathname === '/memory/history') {
    const query = url.searchParams.get('q') || ''
    const limit = parseInt(url.searchParams.get('limit')) || 20
    if (query) {
      const results = memory.searchHistory(query, limit)
      res.end(JSON.stringify(results, null, 2))
    } else {
      const history = memory.loadHistory().slice(-limit)
      res.end(JSON.stringify(history, null, 2))
    }
    return
  }

  // GET /profiles — list all device profiles with operations
  if (req.method === 'GET' && url.pathname === '/profiles') {
    const result = []
    for (const [id, p] of profiles) {
      result.push({
        id: p.vendor_device,
        name: p.name,
        type: p.type,
        arch: p.arch,
        operations: (p.operations || []).map(op => ({ name: op.name, desc: op.desc })),
      })
    }
    res.end(JSON.stringify(result, null, 2))
    return
  }

  // GET /dashboard — edge monitoring dashboard
  if (req.method === 'GET' && url.pathname === '/dashboard') {
    const dashboard = {}
    for (const [id, node] of nodes) {
      dashboard[id] = {
        status: node.status,
        endpoint: node.endpoint,
        arch: node.arch,
        last_seen: node.last_seen,
        health: node.health || null,
        history_count: (edgeHistory.get(id) || []).length,
      }
    }
    res.end(JSON.stringify({ edges: dashboard, timestamp: new Date().toISOString() }, null, 2))
    return
  }

  // GET /dashboard/:node_id — specific edge history
  if (req.method === 'GET' && url.pathname.startsWith('/dashboard/')) {
    const nodeId = url.pathname.split('/')[2]
    const history = edgeHistory.get(nodeId) || []
    res.end(JSON.stringify({ node_id: nodeId, history }, null, 2))
    return
  }

  // GET /scenario — scenario test UI
  if (req.method === 'GET' && url.pathname === '/scenario') {
    res.setHeader('Content-Type', 'text/html')
    res.end(require('fs').readFileSync(__dirname + '/../web/scenario/index.html', 'utf8'))
    return
  }

  // POST /scenario/deploy — deploy a scenario monitor
  if (req.method === 'POST' && url.pathname === '/scenario/deploy') {
    const data = safeJSON(body) || {}
    const edge = [...nodes.values()].find(n => n.arch === 'i386' && n.status === 'alive')
    if (!edge) { jsonError(res, 404, 'no x86 edge available'); return }

    try {
      const { compileAssembly: compile } = require('./compiler')
      const { deployMonitor: deploy } = require('./transport')

      // Temperature monitor: read vreg[0] (temp)
      const bin = await compile('BITS 32\nmov eax, [0x200000]\nret')
      const hubPort = parseInt(process.env.PORT || '9000')
      const result = await deploy(edge.endpoint, bin, 3000, 0, 35, '10.0.2.2', hubPort)
      res.end(JSON.stringify({ ok: true, result, edge: edge.node_id }))
    } catch (e) {
      jsonError(res, 500, e.message)
    }
    return
  }

  // GET /scenario/health — proxy edge health for UI (avoids CORS)
  if (req.method === 'GET' && url.pathname === '/scenario/health') {
    const edge = [...nodes.values()].find(n => n.arch === 'i386' && n.status === 'alive')
    if (!edge) { jsonError(res, 404, 'no edge'); return }
    try {
      const http2 = require('http')
      const edgeRes = await new Promise((resolve, reject) => {
        http2.get(edge.endpoint + '/health', { timeout: 3000 }, (r) => {
          let d = ''; r.on('data', c => d += c); r.on('end', () => resolve(d))
        }).on('error', reject)
      })
      res.end(edgeRes)
    } catch (e) { jsonError(res, 502, 'edge unreachable') }
    return
  }

  // POST /scenario/reset — reset virtual registers to initial state
  if (req.method === 'POST' && url.pathname === '/scenario/reset') {
    const edge = [...nodes.values()].find(n => n.arch === 'i386' && n.status === 'alive')
    if (!edge) { jsonError(res, 404, 'no x86 edge available'); return }

    try {
      const { compileAssembly: compile } = require('./compiler')
      const { pokeNode: poke } = require('./transport')

      // Reset all vregs: temp=22, cpu=40, cooling=1, power=800, all servers ON
      const asm = `BITS 32
mov dword [0x200000], 22
mov dword [0x200004], 40
mov dword [0x200008], 1
mov dword [0x20000C], 800
mov dword [0x200010], 1
mov dword [0x200014], 1
mov dword [0x200018], 1
mov dword [0x20001C], 1
mov dword [0x200020], 1
mov dword [0x200024], 1
mov dword [0x200028], 0
ret`
      const bin = await compile(asm)
      const result = await poke(edge.endpoint, bin)
      res.end(JSON.stringify({ ok: true, result }))
    } catch (e) {
      jsonError(res, 500, e.message)
    }
    return
  }

  // GET /factory — factory simulation UI
  if (req.method === 'GET' && url.pathname === '/factory') {
    res.setHeader('Content-Type', 'text/html')
    res.end(require('fs').readFileSync(__dirname + '/../web/scenario/factory.html', 'utf8'))
    return
  }

  // GET /scenario/factory/health?line=A — proxy specific line health
  if (req.method === 'GET' && url.pathname === '/scenario/factory/health') {
    const line = url.searchParams.get('line')
    const nodeId = 'line-' + line
    const node = nodes.get(nodeId)
    if (!node) { jsonError(res, 404, 'line not found: ' + nodeId); return }
    try {
      const http2 = require('http')
      const edgeRes = await new Promise((resolve, reject) => {
        http2.get(node.endpoint + '/health', { timeout: 3000 }, (r) => {
          let d = ''; r.on('data', c => d += c); r.on('end', () => resolve(d))
        }).on('error', reject)
      })
      res.end(edgeRes)
    } catch (e) { jsonError(res, 502, 'line unreachable') }
    return
  }

  // POST /scenario/factory/start — deploy monitors on all lines
  if (req.method === 'POST' && url.pathname === '/scenario/factory/start') {
    const lines = ['line-A', 'line-B', 'line-C']
    const results = []
    const { compileAssembly: compile } = require('./compiler')
    const { deployMonitor: deploy } = require('./transport')
    const hubPort = parseInt(process.env.PORT || '3333')

    for (const lineId of lines) {
      const node = nodes.get(lineId)
      if (!node || node.status !== 'alive') { results.push({ line: lineId, error: 'offline' }); continue }
      try {
        // Monitor: read defect rate (vreg index 10 = alert, reused as defect %)
        const bin = await compile('BITS 32\nmov eax, [0x200028]\nret')
        const r = await deploy(node.endpoint, bin, 3000, 0, 3, '10.0.2.2', hubPort)
        results.push({ line: lineId, result: r })
      } catch (e) {
        results.push({ line: lineId, error: e.message })
      }
    }
    res.end(JSON.stringify({ ok: true, results }))
    return
  }

  // POST /scenario/factory/reset — reset all lines
  if (req.method === 'POST' && url.pathname === '/scenario/factory/reset') {
    const lines = ['line-A', 'line-B', 'line-C']
    const { compileAssembly: compile } = require('./compiler')
    const { pokeNode: poke } = require('./transport')

    const configs = {
      'line-A': { speed: 100, defect: 2, temp: 25 },
      'line-B': { speed: 100, defect: 1, temp: 23 },
      'line-C': { speed: 80,  defect: 5, temp: 30 },
    }

    for (const lineId of lines) {
      const node = nodes.get(lineId)
      if (!node || node.status !== 'alive') continue
      const cfg = configs[lineId]
      try {
        const asm = `BITS 32
mov dword [0x200000], ${cfg.temp}
mov dword [0x200004], ${cfg.speed}
mov dword [0x200008], 1
mov dword [0x20000C], ${cfg.speed * 8}
mov dword [0x200010], 1
mov dword [0x200014], 1
mov dword [0x200018], 1
mov dword [0x20001C], 1
mov dword [0x200020], 1
mov dword [0x200024], 1
mov dword [0x200028], ${cfg.defect}
ret`
        const bin = await compile(asm)
        await poke(node.endpoint, bin)
      } catch (e) { log.warn(`[factory] reset ${lineId} failed: ${e.message}`) }
    }
    res.end(JSON.stringify({ ok: true }))
    return
  }

  // POST /scenario/factory/shutdown?line=C — shutdown a production line (QEMU halts)
  if (req.method === 'POST' && url.pathname === '/scenario/factory/shutdown') {
    const line = url.searchParams.get('line')
    const nodeId = 'line-' + line
    const node = nodes.get(nodeId)
    if (!node) { jsonError(res, 404, 'line not found'); return }

    const { shutdownEdge } = require('./transport')
    try {
      const result = await shutdownEdge(node.endpoint)
      node.status = 'dead'
      log.info(`[factory] line ${line} SHUTDOWN: ${result}`)
      res.end(JSON.stringify({ ok: true, line: nodeId, result }))
    } catch (e) {
      jsonError(res, 500, e.message)
    }
    return
  }

  // POST /scenario/factory/restart?line=C — restart a stopped line
  if (req.method === 'POST' && url.pathname === '/scenario/factory/restart') {
    const line = url.searchParams.get('line')
    const containerName = 'poke-edge-line-' + line.toLowerCase() + '-1'
    try {
      const { execSync } = require('child_process')
      execSync(`docker restart ${containerName}`, { timeout: 30000 })
      log.info(`[factory] line ${line} RESTARTED via docker`)
      // Re-enroll after restart (edge needs time to boot)
      setTimeout(() => {
        const node = nodes.get('line-' + line)
        if (node) { node.status = 'alive'; node.last_seen = new Date().toISOString() }
      }, 10000)
      res.end(JSON.stringify({ ok: true, line: 'line-' + line, restarting: true }))
    } catch (e) {
      jsonError(res, 500, 'restart failed: ' + e.message)
    }
    return
  }

  // GET /ui — hub management dashboard
  if (req.method === 'GET' && url.pathname === '/ui') {
    res.setHeader('Content-Type', 'text/html')
    res.end(require('fs').readFileSync(__dirname + '/../web/dashboard/index.html', 'utf8'))
    return
  }

  // GET / or /mobile — mobile POKE edge web UI
  if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/mobile')) {
    res.setHeader('Content-Type', 'text/html')
    res.end(require('fs').readFileSync(__dirname + '/../web/mobile.html', 'utf8'))
    return
  }

  // POST /mobile/register — mobile edge registration
  if (req.method === 'POST' && url.pathname === '/mobile/register') {
    const data = safeJSON(body)
    if (!data || !data.node_id) { jsonError(res, 400, 'invalid body: need node_id'); return }
    const { node_id } = data
    const node = {
      node_id,
      arch: 'mobile',
      memory_mb: 0,
      capabilities: ['graphics', 'input'],
      endpoint: `polling:${node_id}`,
      status: 'alive',
      last_seen: new Date().toISOString(),
      pending_img: null,
    }
    nodes.set(node_id, node)
    log.info(`[mobile] registered: ${node_id}`)
    res.end(JSON.stringify({ ok: true }))
    return
  }

  // GET /mobile/poll/:id — mobile image polling
  if (req.method === 'GET' && url.pathname.startsWith('/mobile/poll/')) {
    const nodeId = url.pathname.split('/')[3]
    const node = nodes.get(nodeId)
    if (node && node.pending_img) {
      const imgBuf = node.pending_img
      node.pending_img = null
      res.end(JSON.stringify({ pending: true, img: imgBuf.toString('base64'), width: node.pending_w || 100, height: node.pending_h || 100 }))
    } else {
      res.end(JSON.stringify({ pending: false }))
    }
    return
  }

  // POST /voice — voice data -> STT -> relay
  if (req.method === 'POST' && url.pathname === '/voice') {
    if (!checkAuth(req, res)) return
    try {
      const audioData = Buffer.from(body, 'binary')
      const fromId = url.searchParams.get('from') || 'arm-qemu'

      log.info(`[voice] ${audioData.length} bytes from ${fromId}`)

      const fs = require('fs')
      const { execFileSync } = require('child_process')
      const os = require('os')
      const path = require('path')

      // Secure temp directory
      const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'poke-voice-'))
      const tmpWav = path.join(tmpDir, 'input.wav')
      const tmpAiff = path.join(tmpDir, 'reply.aiff')
      const replyWav = path.join(tmpDir, 'reply.wav')
      fs.writeFileSync(tmpWav, audioData, { mode: 0o600 })

      // STT: hint parameter (browser Web Speech API handles real STT)
      let transcript = url.searchParams.get('hint') || ''
      if (!transcript) {
        try {
          const sttScript = `
import sys
try:
    import speech_recognition as sr
    r = sr.Recognizer()
    with sr.AudioFile(sys.argv[1]) as src:
        audio = r.record(src)
    print(r.recognize_google(audio, language='ko-KR'))
except Exception as e:
    print('STT_FAIL:' + str(e))
`
          transcript = execFileSync('python3', ['-c', sttScript, tmpWav], { timeout: 15000 }).toString().trim()
          if (transcript.startsWith('STT_FAIL')) transcript = ''
        } catch (e) {
          log.warn(`[voice] STT fallback failed: ${e.message}`)
          transcript = ''
        }
      }

      if (!transcript) {
        fs.rmSync(tmpDir, { recursive: true, force: true })
        jsonError(res, 400, 'STT failed and no hint provided')
        return
      }

      log.info(`[voice] transcript: "${transcript}"`)

      const fromNode = nodes.get(fromId)
      if (!fromNode) { fs.rmSync(tmpDir, { recursive: true, force: true }); jsonError(res, 404, 'unknown sender'); return }

      const plan = await planCommand(transcript, fromId, null, [...nodes.values()])
      log.debug(`[voice] plan: ${JSON.stringify(plan)}`)

      let result = null
      if (plan.type === 'compute') {
        const targetNode = nodes.get(plan.target)
        if (targetNode) {
          const asmCode = await generateAssembly(plan.task)
          if (asmCode) {
            const bin = await compileAssembly(asmCode)
            if (bin) result = await pokeNode(targetNode.endpoint, bin)
          }
        }
      }

      // TTS response
      let replyText = result && typeof result === 'string'
        ? result.replace('eax=', 'Result: ').replace('\n', '')
        : 'Done'

      let audioReply = null
      try {
        execFileSync('say', ['-v', 'Yuna', '-o', tmpAiff, replyText], { timeout: 10000 })
        execFileSync('afconvert', ['-f', 'WAVE', '-d', 'LEI16@8000', '-c', '1', tmpAiff, replyWav], { timeout: 10000 })
        if (fs.existsSync(replyWav)) {
          audioReply = fs.readFileSync(replyWav).toString('base64')
        }
      } catch (e) {
        log.warn(`[voice] TTS failed: ${e.message}`)
      }

      // Cleanup temp
      fs.rmSync(tmpDir, { recursive: true, force: true })

      res.end(JSON.stringify({
        transcript, plan, result,
        reply_text: replyText,
        reply_audio_base64: audioReply,
        reply_audio_size: audioReply ? Buffer.from(audioReply, 'base64').length : 0
      }, null, 2))
    } catch (err) {
      log.error(`[voice] ERROR: ${err.message}`)
      jsonError(res, 500, err.message)
    }
    return
  }

  // POST /camera — multimodal: image → LLM vision analysis → agent action
  if (req.method === 'POST' && url.pathname === '/camera') {
    if (!checkAuth(req, res)) return
    const data = safeJSON(body)
    if (!data) { jsonError(res, 400, 'invalid JSON body'); return }

    const { image_url, image_base64, instruction, from, target } = data
    if (!image_url && !image_base64) { jsonError(res, 400, 'need image_url or image_base64'); return }

    const fromId = from || 'hub'
    log.info(`[camera] image analysis request from=${fromId}`)

    // Build the agent command with vision context
    const visionInstruction = instruction || 'Analyze this image and take appropriate hardware action on the target edge.'
    const command = `[VISION REQUEST] ${visionInstruction}\nImage provided via ${image_url ? 'URL: ' + image_url : 'base64 data (' + (image_base64 || '').length + ' chars)'}.\nUse the analyze_image tool first to understand the image, then decide on follow-up actions.`

    try {
      const result = await agentLoop(command, fromId, target)
      res.end(JSON.stringify({ from: fromId, instruction: visionInstruction, ...result }, null, 2))
    } catch (err) {
      log.error(`[camera] ERROR: ${err.message}`)
      jsonError(res, 500, err.message)
    }
    return
  }

  res.statusCode = 404
  res.end(JSON.stringify({ error: 'not found' }))
}

function createServer() {
  // Register monitor trigger callback → auto agentLoop
  setMonitorTriggerCallback((edgeId, mon) => {
    const node = nodes.get(edgeId)
    const vregs = node?.health?.vregs || {}
    // Collect status of all edges for multi-line awareness
    const allEdges = [...nodes.entries()].map(([id, n]) => `${id}: ${n.status}`).join(', ')
    const vregStr = Object.entries(vregs).map(([k,v]) => `${k}=${v}`).join(', ')

    const command = `AUTONOMOUS EVENT from edge "${edgeId}": ` +
      `Monitor ${mon.id} triggered with value=${mon.val}. ` +
      `Current state: ${vregStr}. ` +
      `All edges: ${allEdges}. ` +
      `Virtual registers at address 0x200000 (u32 each, offset in bytes): ` +
      `+0=temp, +4=cpu_load, +8=cooling(0/1/2), +12=power, ` +
      `+16=web_srv, +20=db_srv, +24=cache_srv, +28=log_srv, +32=backup_srv, +36=dev_srv, +40=alert. ` +
      `Server priority: web,db=CRITICAL > cache=IMPORTANT > log=NORMAL > backup,dev=LOW. ` +
      `You have these tools: ` +
      `1) execute_x86 — modify virtual registers (e.g. cooling, speed). ` +
      `2) shutdown_line — ACTUALLY kill a QEMU edge (use for LOW priority only!). ` +
      `3) restart_line — restart a previously killed edge. ` +
      `Analyze cascade risk. If CRITICAL alert: increase cooling, shut down LOW priority LINES (shutdown_line), ` +
      `protect CRITICAL services. If alert normalizes later: restart_line to bring them back. ` +
      `Use execute_x86: "BITS 32 / mov dword [0x200000+offset], value / ret".`

    log.info(`[event] auto-firing agentLoop for ${edgeId} monitor ${mon.id}`)
    agentLoop(command, edgeId, edgeId).then(result => {
      log.info(`[event] result: ${result.result?.slice(0, 100)}`)
    }).catch(err => {
      log.warn(`[event] failed: ${err.message}`)
    })
  })

  return http.createServer(handleRequest)
}

module.exports = { createServer, handleRequest, checkAuth, safeJSON, jsonError }
