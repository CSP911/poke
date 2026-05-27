/**
 * POKE Hub — 노드 관리 + LLM 코드 생성 + 실행
 *
 * 사용법: node hub.js
 * 포트: 3333
 */

require('dotenv').config()
const http = require('http')
const { assemble } = require('./asm.js')

process.on('uncaughtException', (err) => { console.error('[FATAL]', err.message) })
process.on('unhandledRejection', (err) => { console.error('[REJECT]', err.message || err) })

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
  catch { return null }
}

function jsonError(res, status, msg) {
  res.statusCode = status
  res.end(JSON.stringify({ error: msg }))
}

// ── 노드 레지스트리 ──
const nodes = new Map()

// ── 디바이스 프로파일 DB ──
const profiles = new Map()
function loadProfiles() {
  const fs = require('fs')
  const dir = __dirname + '/profiles'
  if (!fs.existsSync(dir)) return
  for (const f of fs.readdirSync(dir).filter(f => f.endsWith('.json'))) {
    try {
      const p = JSON.parse(fs.readFileSync(dir + '/' + f, 'utf8'))
      profiles.set(p.vendor_device, p)
    } catch {}
  }
  console.log(`[profiles] loaded ${profiles.size} device profiles`)
}
loadProfiles()

function getProfileSummary() {
  if (profiles.size === 0) return 'No device profiles loaded.'
  return [...profiles.values()].map(p =>
    `${p.vendor_device} ${p.name} (${p.type}) BAR0=0x${(p.bar0?.address >>> 0).toString(16)}`
  ).join('\n')
}

// ── 허브 API ──
async function handleRequest(req, res) {
  const url = new URL(req.url, `http://${req.headers.host}`)
  let body = ''
  for await (const chunk of req) body += chunk

  // CORS
  res.setHeader('Content-Type', 'application/json')

  // POST /enroll — 노드 등록
  if (req.method === 'POST' && url.pathname === '/enroll') {
    if (!checkAuth(req, res)) return
    const node = safeJSON(body)
    if (!node || !node.node_id || !node.endpoint) { jsonError(res, 400, 'invalid body: need node_id, endpoint'); return }
    node.status = 'alive'
    node.last_seen = new Date().toISOString()
    nodes.set(node.node_id, node)
    console.log(`[enroll] ${node.node_id} @ ${node.endpoint} (${node.arch}, ${node.memory_mb}MB)`)
    res.end(JSON.stringify({ ok: true, enrolled: node.node_id }))
    return
  }

  // GET /nodes — 노드 목록
  if (req.method === 'GET' && url.pathname === '/nodes') {
    res.end(JSON.stringify({ nodes: [...nodes.values()] }, null, 2))
    return
  }

  // POST /run — 자연어 → LLM → 코드 생성 → 노드 실행
  if (req.method === 'POST' && url.pathname === '/run') {
    if (!checkAuth(req, res)) return
    const data = safeJSON(body)
    if (!data || !data.task) { jsonError(res, 400, 'invalid body: need task'); return }
    const { task, node_id } = data

    // 노드 선택
    let target = node_id ? nodes.get(node_id) : [...nodes.values()].find(n => n.status === 'alive')
    if (!target) {
      res.end(JSON.stringify({ error: 'no available node' }))
      return
    }

    console.log(`[run] task="${task}" → ${target.node_id}`)

    // LLM으로 어셈블리 생성
    const asm = await generateAssembly(task)
    if (!asm) {
      res.end(JSON.stringify({ error: 'LLM failed to generate code' }))
      return
    }
    console.log(`[asm]\n${asm}`)

    // nasm으로 컴파일
    const machineCode = await compileAssembly(asm)
    if (!machineCode) {
      res.end(JSON.stringify({ error: 'nasm compilation failed' }))
      return
    }
    console.log(`[compiled] ${machineCode.length} bytes`)

    // 노드에 전송
    const result = await pokeNode(target.endpoint, machineCode)
    console.log(`[result] ${result}`)

    res.end(JSON.stringify({
      task,
      node: target.node_id,
      asm,
      bytes: machineCode.length,
      result
    }, null, 2))
    return
  }

  // POST /draw — 자연어 → LLM이 이미지 생성 코드 → 실행 → POKE에 전송
  if (req.method === 'POST' && url.pathname === '/draw') {
    if (!checkAuth(req, res)) return
    const data = safeJSON(body)
    if (!data || !data.task) { jsonError(res, 400, 'invalid body: need task'); return }
    const { task, node_id } = data
    let target = node_id ? nodes.get(node_id) : [...nodes.values()].find(n => n.status === 'alive')
    if (!target) { res.end(JSON.stringify({ error: 'no available node' })); return }

    console.log(`[draw] task="${task}" → ${target.node_id}`)

    // LLM이 Node.js 이미지 생성 코드를 만듦
    const code = await generateImageCode(task)
    if (!code) { res.end(JSON.stringify({ error: 'LLM failed' })); return }
    console.log(`[draw] code generated (${code.length} chars)`)

    // 코드 실행 → 픽셀 데이터 생성
    const imgResult = runImageCode(code)
    if (!imgResult) { res.end(JSON.stringify({ error: 'code execution failed' })); return }
    console.log(`[draw] image: ${imgResult.width}x${imgResult.height}, ${imgResult.body.length} bytes`)

    // POKE 노드에 IMG 프로토콜로 전송
    const pokeResult = await pokeNodeRaw(target.endpoint, imgResult.body)
    console.log(`[draw] poke result: ${pokeResult}`)

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

  // POST /relay — 에이전트 루프 (observe → think → act → check → loop)
  if (req.method === 'POST' && url.pathname === '/relay') {
    if (!checkAuth(req, res)) return
    const data = safeJSON(body)
    if (!data || !data.from || !data.command) { jsonError(res, 400, 'invalid body: need from, command'); return }
    const { from, command, target } = data
    console.log(`[agent] from=${from} → "${command}"`)

    const fromNode = nodes.get(from)
    if (!fromNode) { jsonError(res, 404, 'unknown sender'); return }

    try {
      const result = await agentLoop(command, from, target)
      res.end(JSON.stringify({ from, command, ...result }, null, 2))
    } catch (err) {
      console.error(`[agent] ERROR: ${err.message}`)
      res.end(JSON.stringify({ error: err.message }))
    }
    return
  }

  // POST /poke-raw — 바이너리 직접 전송 (디버그용)
  if (req.method === 'POST' && url.pathname === '/poke-raw') {
    if (!checkAuth(req, res)) return
    const data = safeJSON(body)
    if (!data || !data.hex) { jsonError(res, 400, 'invalid body: need hex'); return }
    const { node_id, hex } = data
    let target = node_id ? nodes.get(node_id) : [...nodes.values()][0]
    if (!target) { res.end(JSON.stringify({ error: 'no node' })); return }

    const buf = Buffer.from(hex, 'hex')
    const result = await pokeNode(target.endpoint, buf)
    res.end(JSON.stringify({ node: target.node_id, result }))
    return
  }

  // GET /dashboard — 엣지 모니터링 대시보드
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

  // GET /dashboard/:node_id — 특정 엣지 히스토리
  if (req.method === 'GET' && url.pathname.startsWith('/dashboard/')) {
    const nodeId = url.pathname.split('/')[2]
    const history = edgeHistory.get(nodeId) || []
    res.end(JSON.stringify({ node_id: nodeId, history }, null, 2))
    return
  }

  // GET / — 모바일 POKE 엣지 웹 UI
  if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/mobile')) {
    res.setHeader('Content-Type', 'text/html')
    res.end(require('fs').readFileSync(__dirname + '/mobile.html', 'utf8'))
    return
  }

  // POST /mobile/register — 모바일 엣지 등록
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
    console.log(`[mobile] registered: ${node_id}`)
    res.end(JSON.stringify({ ok: true }))
    return
  }

  // GET /mobile/poll/:id — 모바일이 이미지 폴링
  if (req.method === 'GET' && url.pathname.startsWith('/mobile/poll/')) {
    const nodeId = url.pathname.split('/')[3]
    const node = nodes.get(nodeId)
    if (node && node.pending_img) {
      const imgBuf = node.pending_img
      node.pending_img = null
      // base64로 전달
      res.end(JSON.stringify({ pending: true, img: imgBuf.toString('base64'), width: node.pending_w || 100, height: node.pending_h || 100 }))
    } else {
      res.end(JSON.stringify({ pending: false }))
    }
    return
  }

  // POST /voice — 음성 데이터 수신 → STT → relay 처리
  if (req.method === 'POST' && url.pathname === '/voice') {
    if (!checkAuth(req, res)) return
    try {
      const audioData = Buffer.from(body, 'binary')
      const fromId = url.searchParams.get('from') || 'arm-qemu'

      console.log(`[voice] ${audioData.length} bytes from ${fromId}`)

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
        // Try Python speech_recognition if available
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
        } catch { transcript = '' }
      }

      if (!transcript) {
        fs.rmSync(tmpDir, { recursive: true, force: true })
        jsonError(res, 400, 'STT failed and no hint provided')
        return
      }

      console.log(`[voice] transcript: "${transcript}"`)

      const fromNode = nodes.get(fromId)
      if (!fromNode) { fs.rmSync(tmpDir, { recursive: true, force: true }); jsonError(res, 404, 'unknown sender'); return }

      const plan = await planCommand(transcript, fromId, null, [...nodes.values()])
      console.log(`[voice] plan: ${JSON.stringify(plan)}`)

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

      // TTS response (safe: execFileSync with array args, no shell)
      let replyText = result && typeof result === 'string'
        ? result.replace('eax=', '결과는 ').replace('\n', '') + '입니다'
        : '처리했습니다'

      let audioReply = null
      try {
        execFileSync('say', ['-v', 'Yuna', '-o', tmpAiff, replyText], { timeout: 10000 })
        execFileSync('afconvert', ['-f', 'WAVE', '-d', 'LEI16@8000', '-c', '1', tmpAiff, replyWav], { timeout: 10000 })
        if (fs.existsSync(replyWav)) {
          audioReply = fs.readFileSync(replyWav).toString('base64')
        }
      } catch (e) {
        console.warn(`[voice] TTS failed: ${e.message}`)
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
      console.error(`[voice] ERROR: ${err.message}`)
      jsonError(res, 500, err.message)
    }
    return
  }

  res.statusCode = 404
  res.end(JSON.stringify({ error: 'not found' }))
}

const server = http.createServer(handleRequest)

// ── LLM: 명령 해석 (relay용) ──
async function planCommand(command, fromId, targetId, nodeList) {
  const Anthropic = require('@anthropic-ai/sdk')
  const client = new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY })

  const nodeDesc = nodeList.map(n =>
    `${n.node_id}: arch=${n.arch}, capabilities=${(n.capabilities||[]).join(',')}`
  ).join('\n')

  const msg = await client.messages.create({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 300,
    system: `You interpret commands sent from one edge device to a hub.
The hub controls multiple edge devices.

Available edges:
${nodeDesc}

The command comes from edge "${fromId}".
${targetId ? `Requested target: "${targetId}". YOU MUST use this target.` : 'No specific target requested.'}

Respond with JSON only:
{
  "type": "compute" | "draw" | "answer" | "device" | "broadcast",
  "target": "node_id to execute on",
  "task": "the specific task description"
}

Types:
- "compute": math/calculation → target runs assembly code. Target MUST be x86 or ARM edge, NEVER mobile.
- "draw": visual/image → generate pixel art and show on target.
- "answer": question/conversation that needs NO code execution. Just answer with text.
- "device": hardware control (read sensor, toggle GPIO, control peripheral). Target = the edge with the device.
- "broadcast": show on all edges.

Rules:
- If command is a math calculation → type=compute, target=x86-qemu
- If command asks to show/draw/display something → type=draw
- If command is a general question, explanation, or conversation ("what is...", "explain...", "tell me...", "뭐야", "알려줘", "설명해") → type=answer. NEVER draw or compute for questions.
- If command involves hardware (LED, sensor, temperature, GPIO, device) → type=device
- Mobile edges (arch=mobile) can only receive images or text, never run assembly
- Default compute target: x86-qemu (i386)
- task should be a clear description for code/answer generation`,
    messages: [{ role: 'user', content: command }],
  })

  let text = msg.content[0].type === 'text' ? msg.content[0].text.trim() : '{}'
  text = text.replace(/^```\w*\n?/, '').replace(/\n?```$/, '').trim()
  try { return JSON.parse(text) }
  catch { return { type: 'reply', target: fromId, task: command } }
}

// ══════════════════════════════════════════
// ── Agent Loop (observe → think → act → check)
// ══════════════════════════════════════════

// ── 기본 도구 (항상 존재) ──
const BASE_TOOLS = [
  {
    name: 'list_edges',
    description: 'List all connected edge devices with their architecture, capabilities, and status.',
    input_schema: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'list_devices',
    description: 'List all known hardware devices with their available operations.',
    input_schema: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'execute_x86',
    description: 'Compile and execute x86 (i386, NASM) assembly on a target edge. Returns eax value. MUST start with "BITS 32". Return in EAX. End with RET.',
    input_schema: {
      type: 'object',
      properties: { target: { type: 'string' }, asm_code: { type: 'string' } },
      required: ['target', 'asm_code'],
    },
  },
  {
    name: 'execute_arm',
    description: 'Compile and execute ARM64 (AArch64, GNU as) assembly on a target edge. Returns x0 value. Return in X0, end with RET. No directives.',
    input_schema: {
      type: 'object',
      properties: { target: { type: 'string' }, asm_code: { type: 'string' } },
      required: ['target', 'asm_code'],
    },
  },
  {
    name: 'draw_image',
    description: 'Generate pixel art image using Node.js code and send to a target edge. Code must define W, H, pixels Buffer, setPixel(x,y,r,g,b), and end with module.exports={W,H,pixels}.',
    input_schema: {
      type: 'object',
      properties: { target: { type: 'string' }, js_code: { type: 'string' } },
      required: ['target', 'js_code'],
    },
  },
  {
    name: 'build_and_deploy',
    description: `Build a C program and deploy it to an edge device. Use for complex tasks: monitoring, data collection, automation, multi-step hardware interaction, string processing, etc.

The C code runs in bare-metal (freestanding, no libc). Entry: void _start(char *buf, int *len).
Write results to buf, set *len = bytes written.

POKE SDK headers available (use #include):
- <poke/io.h>     — u8/u16/u32 types, inb/outb/inl/outl, mmio_read32/write32, poke_delay
- <poke/string.h> — poke_strlen, poke_memcpy, poke_memset, poke_strcmp, poke_strcpy, poke_strcat
- <poke/format.h> — poke_itoa(val,buf), poke_utoh(val,buf,digits), poke_btoh(byte,buf), poke_format_mac(mac6,buf), poke_format_ip(ip4,buf)
- <poke/pci.h>    — pci_read(bus,slot,func,off), pci_vendor/device/bar0/class(bus,slot), pci_scan(callback,ctx)
- <poke/net.h>    — e1000_t, e1000_init/read/write, e1000_link_up, e1000_read_mac(dev,mac6)

Example using SDK:
  #include <poke/net.h>
  #include <poke/format.h>
  void _start(char *buf, int *len) {
    e1000_t nic; e1000_init(&nic, 0xfebc0000);
    unsigned char mac[6]; e1000_read_mac(&nic, mac);
    *len = poke_format_mac(mac, buf);
  }`,
    input_schema: {
      type: 'object',
      properties: {
        target: { type: 'string', description: 'Edge node ID' },
        c_code: { type: 'string', description: 'C source code (freestanding, entry = _start(char*buf, int*len))' },
        description: { type: 'string', description: 'What this binary does' },
      },
      required: ['target', 'c_code'],
    },
  },
  {
    name: 'fetch_url',
    description: 'Fetch data from a URL (HTTP GET). Use for weather, APIs, external data. Returns text body. Example URLs: "wttr.in/Seoul?format=j1" for weather JSON, "wttr.in/Seoul?format=3" for one-line weather.',
    input_schema: {
      type: 'object',
      properties: { url: { type: 'string', description: 'URL to fetch' } },
      required: ['url'],
    },
  },
  {
    name: 'reply_text',
    description: 'Send a text reply back to the user. Use this for questions, explanations, or status updates.',
    input_schema: {
      type: 'object',
      properties: { text: { type: 'string' } },
      required: ['text'],
    },
  },
]

// ── 프로파일에서 자동 생성된 도구 ──
function generateDeviceTools() {
  const tools = []
  for (const [id, profile] of profiles) {
    if (!profile.operations) continue
    for (const op of profile.operations) {
      const toolName = `${profile.type}_${op.name}`
      tools.push({
        name: toolName,
        description: `[${profile.name}] ${op.desc}. Returns: ${op.returns}`,
        input_schema: {
          type: 'object',
          properties: {
            target: { type: 'string', description: 'Edge node to execute on (default: x86-qemu)' },
          },
          required: [],
        },
        _profile: id,
        _asm: op.asm,
        _arch: profile.arch || 'i386',
      })
    }
  }
  return tools
}

function getAgentTools() {
  return [...BASE_TOOLS, ...generateDeviceTools()]
}

function getDeviceToolsSummary() {
  const devTools = generateDeviceTools()
  if (devTools.length === 0) return ''
  return '\nAuto-generated device tools (call directly, no assembly needed):\n' +
    devTools.map(t => `- ${t.name}: ${t.description}`).join('\n')
}

async function executeAgentTool(toolName, toolInput) {
  console.log(`[agent] tool: ${toolName}(${JSON.stringify(toolInput).slice(0, 100)})`)

  // ── 자동 생성된 디바이스 도구 확인 ──
  const devTools = generateDeviceTools()
  const devTool = devTools.find(t => t.name === toolName)
  if (devTool) {
    const target = toolInput.target || 'x86-qemu'
    const node = nodes.get(target)
    if (!node) return `Error: edge "${target}" not found`

    console.log(`[agent] device-op: ${toolName} on ${target} (${devTool._arch})`)

    if (devTool._arch === 'aarch64') {
      const bin = await compileAssemblyARM(devTool._asm)
      if (!bin) return 'Error: ARM compilation failed'
      return await pokeNodeARM(node.endpoint, bin)
    } else {
      const bin = await compileAssembly(devTool._asm)
      if (!bin) return 'Error: NASM compilation failed'
      return await pokeNode(node.endpoint, bin)
    }
  }

  // ── 기본 도구 ──
  if (toolName === 'list_edges') {
    const edgeList = [...nodes.values()].map(n => ({
      node_id: n.node_id, arch: n.arch, endpoint: n.endpoint,
      status: n.status, capabilities: n.capabilities || [],
    }))
    return JSON.stringify(edgeList, null, 2)
  }

  if (toolName === 'list_devices') {
    const devList = [...profiles.values()].map(p => {
      const ops = (p.operations || []).map(o => `${p.type}_${o.name}`).join(', ')
      return `${p.vendor_device} ${p.name} [${p.type}] → operations: ${ops || 'none'}`
    }).join('\n')
    return devList || 'No device profiles loaded.'
  }

  if (toolName === 'execute_x86') {
    const node = nodes.get(toolInput.target)
    if (!node) return `Error: edge "${toolInput.target}" not found`
    const bin = await compileAssembly(toolInput.asm_code)
    if (!bin) return 'Error: NASM compilation failed'
    return await pokeNode(node.endpoint, bin)
  }

  if (toolName === 'execute_arm') {
    const node = nodes.get(toolInput.target)
    if (!node) return `Error: edge "${toolInput.target}" not found`
    const bin = await compileAssemblyARM(toolInput.asm_code)
    if (!bin) return 'Error: ARM assembly compilation failed'
    return await pokeNodeARM(node.endpoint, bin)
  }

  if (toolName === 'draw_image') {
    const imgResult = runImageCode(toolInput.js_code)
    if (!imgResult) return 'Error: image generation failed'

    const node = nodes.get(toolInput.target)
    if (!node) return `Error: edge "${toolInput.target}" not found`

    if (node.endpoint.startsWith('polling:')) {
      node.pending_img = imgResult.body.slice(7)
      node.pending_w = imgResult.width
      node.pending_h = imgResult.height
      return `Image ${imgResult.width}x${imgResult.height} queued for ${toolInput.target}`
    } else {
      return await pokeNodeRaw(node.endpoint, imgResult.body)
    }
  }

  if (toolName === 'build_and_deploy') {
    const node = nodes.get(toolInput.target)
    if (!node) return `Error: edge "${toolInput.target}" not found`
    const arch = node.arch || 'i386'
    const fs = require('fs')
    const { execSync } = require('child_process')

    // i/o helper header for x86
    const x86Header = `
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
static inline u8 inb(u16 port) { u8 v; __asm__ volatile("inb %1,%0":"=a"(v):"Nd"(port)); return v; }
static inline u16 inw(u16 port) { u16 v; __asm__ volatile("inw %1,%0":"=a"(v):"Nd"(port)); return v; }
static inline u32 inl(u16 port) { u32 v; __asm__ volatile("inl %1,%0":"=a"(v):"Nd"(port)); return v; }
static inline void outb(u16 port, u8 val) { __asm__ volatile("outb %0,%1"::"a"(val),"Nd"(port)); }
static inline void outw(u16 port, u16 val) { __asm__ volatile("outw %0,%1"::"a"(val),"Nd"(port)); }
static inline void outl(u16 port, u32 val) { __asm__ volatile("outl %0,%1"::"a"(val),"Nd"(port)); }
`
    const armHeader = `
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long u64;
`

    const sdkInclude = __dirname + '/sdk/include'
    const fullCode = toolInput.c_code
    const tmpC = '/tmp/poke_deploy.c'
    const tmpObj = '/tmp/poke_deploy.o'
    const tmpBin = '/tmp/poke_deploy.bin'

    fs.writeFileSync(tmpC, fullCode)

    try {
      if (arch === 'aarch64') {
        execSync(`aarch64-elf-gcc -I${sdkInclude} -ffreestanding -nostdlib -fno-builtin -O1 -c -o ${tmpObj} ${tmpC} 2>&1`)
        execSync(`aarch64-elf-objcopy -O binary -j .text ${tmpObj} ${tmpBin} 2>&1`)
      } else {
        execSync(`i686-elf-gcc -I${sdkInclude} -ffreestanding -nostdlib -fno-builtin -O1 -c -o ${tmpObj} ${tmpC} 2>&1`)
        execSync(`i686-elf-objcopy -O binary -j .text ${tmpObj} ${tmpBin} 2>&1`)
      }
    } catch (e) {
      const stderr = e.stderr ? e.stderr.toString() : e.message
      console.error('[build] compile error:', stderr)
      return 'Compile error: ' + stderr.slice(0, 500)
    }

    const bin = fs.readFileSync(tmpBin)
    const binSize = bin.length
    console.log(`[build] compiled ${binSize} bytes for ${arch}`)

    let result
    if (arch === 'aarch64') {
      result = await pokeNodeARM(node.endpoint, bin)
    } else {
      result = await pokeNode(node.endpoint, bin)
    }

    return `Binary deployed: ${binSize} bytes to ${toolInput.target} (${arch})\nResult: ${result}`
  }

  if (toolName === 'fetch_url') {
    return new Promise((resolve) => {
      let fetchUrl = toolInput.url
      if (!fetchUrl.startsWith('http')) fetchUrl = 'http://' + fetchUrl
      const mod = fetchUrl.startsWith('https') ? require('https') : require('http')
      mod.get(fetchUrl, { timeout: 10000, headers: { 'User-Agent': 'curl/7.0' } }, (res) => {
        let data = ''
        res.on('data', c => data += c)
        res.on('end', () => resolve(data.slice(0, 3000)))  // 3KB 제한
      }).on('error', e => resolve('Fetch error: ' + e.message))
    })
  }

  if (toolName === 'reply_text') {
    return toolInput.text
  }

  return `Unknown tool: ${toolName}`
}

async function agentLoop(command, fromId, targetHint) {
  const Anthropic = require('@anthropic-ai/sdk')
  const client = new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY })

  const edgeList = [...nodes.values()].map(n =>
    `${n.node_id}: arch=${n.arch}, endpoint=${n.endpoint}, caps=${(n.capabilities||[]).join(',')}`
  ).join('\n')

  const systemPrompt = `You are the POKE hub agent. You control edge devices by generating and executing machine code.

Connected edges:
${edgeList}

${targetHint ? `User specified target: ${targetHint}` : ''}

You have tools to:
- List edges and devices
- Execute raw x86 (NASM) or ARM64 (GNU as) assembly on edges
- Call pre-built device operations directly (no assembly needed!)
- Generate and send images to edges
- Reply with text
${getDeviceToolsSummary()}

Rules:
- For math/computation: use execute_x86 or execute_arm based on target architecture.
- x86 assembly: MUST start with "BITS 32" on first line. Return result in EAX. End with RET. No sections, no labels. Example: "BITS 32\nmov eax, 2\nadd eax, 3\nret"
- ARM64 assembly: return in X0, end with RET. No .global, .text directives. Just instructions.
- If code fails (compile error, wrong result), read the error, fix the code, and retry.
- For questions/conversation: use reply_text.
- For images: use draw_image with Node.js code.
- For device interaction: check profiles first with list_profiles or read_profile, then execute.
- Keep it concise. Finish as quickly as possible.
- Respond in the same language as the user's command.
- Max 5 tool calls per request.`

  const messages = [{ role: 'user', content: command }]
  const steps = []
  let finalResult = null
  const MAX_TURNS = 5

  for (let turn = 0; turn < MAX_TURNS; turn++) {
    const response = await client.messages.create({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 1500,
      system: systemPrompt,
      tools: getAgentTools().map(t => ({
        name: t.name, description: t.description, input_schema: t.input_schema,
      })),
      messages,
    })

    // Collect text + tool_use blocks
    const toolUses = response.content.filter(b => b.type === 'tool_use')
    const textBlocks = response.content.filter(b => b.type === 'text')

    if (textBlocks.length > 0) {
      finalResult = textBlocks.map(b => b.text).join('\n')
    }

    // No tool calls → done
    if (toolUses.length === 0) {
      console.log(`[agent] done in ${turn + 1} turns`)
      break
    }

    // Execute each tool
    messages.push({ role: 'assistant', content: response.content })
    const toolResults = []

    for (const tu of toolUses) {
      const result = await executeAgentTool(tu.name, tu.input)
      const resultStr = typeof result === 'string' ? result : JSON.stringify(result)
      console.log(`[agent] ${tu.name} → ${resultStr.slice(0, 80)}`)

      steps.push({ tool: tu.name, input: tu.input, result: resultStr })
      toolResults.push({ type: 'tool_result', tool_use_id: tu.id, content: resultStr })

      // reply_text는 최종 결과
      if (tu.name === 'reply_text') {
        finalResult = tu.input.text
      }
    }

    messages.push({ role: 'user', content: toolResults })

    // stop_reason이 end_turn이면 종료
    if (response.stop_reason === 'end_turn') {
      console.log(`[agent] end_turn at turn ${turn + 1}`)
      break
    }
  }

  return { steps, result: finalResult }
}

// ══════════════════════════════════════════
// ── Legacy LLM functions (used by /run, /voice, /draw)
// ══════════════════════════════════════════

// ── LLM: 텍스트 응답 (코드 실행 불필요) ──
async function generateAnswer(task) {
  const Anthropic = require('@anthropic-ai/sdk')
  const client = new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY })

  const msg = await client.messages.create({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 300,
    system: `You are the POKE hub assistant. Answer briefly in the same language as the question. Keep answers under 2 sentences.`,
    messages: [{ role: 'user', content: task }],
  })
  return msg.content[0].type === 'text' ? msg.content[0].text.trim() : ''
}

// ── LLM: 자연어 → 어셈블리 (아키텍처별) ──
async function generateAssembly(task, arch) {
  const Anthropic = require('@anthropic-ai/sdk')
  const client = new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY })

  const isARM = arch === 'aarch64'
  const system = isARM
    ? `You generate ARM64 (AArch64) assembly code using GNU as syntax.

Rules:
- The code is called as a function (BL target). x0-x7 are scratch registers.
- Return the result in X0.
- End with RET.
- No directives like .global, .text, .section. Just flat instructions.
- No syscalls. Pure computation.
- Output ONLY the assembly code, no explanation.

Example:
  Task: "add 2 and 3"
  Output:
  mov x0, #2
  add x0, x0, #3
  ret`
    : `You generate x86 (i386, 32-bit) NASM assembly code.

Rules:
- The code will be injected into a running OS at an arbitrary address
- Use BITS 32
- The code is called as a function: it can use eax-edx freely
- Return the result in EAX
- End with RET
- No sections, no labels for entry. Just flat code.
- No syscalls. No interrupts. Pure computation.
- Output ONLY the assembly code, no explanation.

Example:
  Task: "add 2 and 3"
  Output:
  BITS 32
  mov eax, 2
  add eax, 3
  ret`

  const msg = await client.messages.create({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 500,
    system,
    messages: [{ role: 'user', content: task }],
  })

  const text = msg.content[0].type === 'text' ? msg.content[0].text.trim() : ''
  return text.replace(/^```\w*\n?/, '').replace(/\n?```$/, '').trim()
}

// ── LLM: 디바이스 제어 코드 생성 (프로파일 참조) ──
async function generateDeviceCode(task, arch, profileCtx) {
  const Anthropic = require('@anthropic-ai/sdk')
  const client = new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY })

  const isARM = arch === 'aarch64'
  const msg = await client.messages.create({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 1000,
    system: `You generate ${isARM ? 'ARM64 (AArch64) assembly (GNU as syntax)' : 'x86 (i386) NASM assembly'} for hardware device control.

Known devices on this system:
${profileCtx}

Rules:
${isARM ? '- Return result in X0, end with RET, no directives.' : '- BITS 32, return in EAX, end with RET.'}
- Use MMIO (memory-mapped I/O) or port I/O as appropriate for the device.
- For MMIO: read/write directly to memory addresses.
- For I/O ports (x86): use in/out with dx for port.
- Output ONLY assembly code, no explanation.`,
    messages: [{ role: 'user', content: task }],
  })

  const text = msg.content[0].type === 'text' ? msg.content[0].text.trim() : ''
  return text.replace(/^```\w*\n?/, '').replace(/\n?```$/, '').trim()
}

// ── ARM64 어셈블리 컴파일 ──
async function compileAssemblyARM(asm) {
  const fs = require('fs')
  const { execSync } = require('child_process')
  const tmpAsm = '/tmp/poke_arm.s'
  const tmpObj = '/tmp/poke_arm.o'
  const tmpBin = '/tmp/poke_arm.bin'

  fs.writeFileSync(tmpAsm, asm)
  try {
    execSync(`aarch64-elf-as -o ${tmpObj} ${tmpAsm} 2>&1`)
    execSync(`aarch64-elf-objcopy -O binary ${tmpObj} ${tmpBin} 2>&1`)
    return fs.readFileSync(tmpBin)
  } catch (e) {
    console.error('[arm-as error]', e.message)
    return null
  }
}

// ── ARM POKE 노드에 코드 전송 (serial protocol over TCP) ──
function pokeNodeARM(endpoint, machineCode) {
  return new Promise((resolve) => {
    const net = require('net')
    // endpoint: "tcp://localhost:8081"
    const match = endpoint.match(/tcp:\/\/([^:]+):(\d+)/)
    if (!match) { resolve('invalid ARM endpoint'); return }

    const sock = new net.Socket()
    sock.connect(parseInt(match[2]), match[1], () => {
      // POKE protocol: "POKE" + len(4 LE) + "EXEC" + code
      const execCmd = Buffer.from('EXEC')
      const payload = Buffer.concat([execCmd, machineCode])

      const header = Buffer.alloc(8)
      header.write('POKE', 0)
      header.writeUInt32LE(payload.length, 4)

      sock.write(Buffer.concat([header, payload]))
    })

    let response = Buffer.alloc(0)
    sock.on('data', (data) => {
      response = Buffer.concat([response, data])
      // Check for complete RESP packet
      if (response.length >= 8 && response.slice(0, 4).toString() === 'RESP') {
        const respLen = response.readUInt32LE(4)
        if (response.length >= 8 + respLen) {
          const result = response.slice(8, 8 + respLen).toString()
          sock.destroy()
          resolve(result)
        }
      }
    })

    sock.on('error', (e) => resolve('ARM error: ' + e.message))
    setTimeout(() => { sock.destroy(); resolve('ARM timeout') }, 10000)
  })
}

// ── LLM: 자연어 → 이미지 생성 Node.js 코드 ──
async function generateImageCode(task) {
  const Anthropic = require('@anthropic-ai/sdk')
  const client = new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY })

  const msg = await client.messages.create({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 2000,
    system: `You generate Node.js code that creates pixel art images.

The code MUST:
1. Define W (width) and H (height), max 200x200
2. Create a Buffer: const pixels = Buffer.alloc(W * H * 3)
3. Define setPixel(x, y, r, g, b) that writes to pixels
4. Draw the requested image using setPixel, loops, and math
5. End with: module.exports = { W, H, pixels }

Helper functions you can define:
- fillCircle(cx, cy, radius, r, g, b)
- fillRect(x, y, w, h, r, g, b)
- fillEllipse(cx, cy, rx, ry, r, g, b)
- drawLine(x1, y1, x2, y2, r, g, b)

Use vibrant colors. Make it visually interesting.
Output ONLY the Node.js code, no explanation, no markdown.`,
    messages: [{ role: 'user', content: task }],
  })

  let text = msg.content[0].type === 'text' ? msg.content[0].text.trim() : ''
  return text.replace(/^```\w*\n?/, '').replace(/\n?```$/, '').trim()
}

// ── 이미지 코드 실행 → IMG 바이너리 생성 ──
function runImageCode(code) {
  const fs = require('fs')
  const { execSync } = require('child_process')
  const tmpFile = '/tmp/poke_draw.js'

  // 코드 끝에 export를 JSON으로 출력하는 래퍼 추가
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

    // IMG 프로토콜: "IMG" + width(2 LE) + height(2 LE) + RGB
    const header = Buffer.alloc(7)
    header[0] = 0x49; header[1] = 0x4D; header[2] = 0x47
    header.writeUInt16LE(W, 3)
    header.writeUInt16LE(H, 5)

    return { width: W, height: H, body: Buffer.concat([header, pixels]) }
  } catch (e) {
    console.error('[draw] execution error:', e.message)
    return null
  }
}

// ── POKE 노드에 바이너리 전송 (IMG) ──
function pokeNodeRaw(endpoint, body) {
  return new Promise((resolve) => {
    const url = new URL('/poke', endpoint)
    const req = http.request({
      hostname: url.hostname, port: url.port, path: '/poke', method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': body.length },
      timeout: 15000,
    }, (res) => {
      let data = ''; res.on('data', c => data += c); res.on('end', () => resolve(data))
    })
    req.on('error', e => resolve('error: ' + e.message))
    req.on('timeout', () => { req.destroy(); resolve('timeout') })
    req.write(body); req.end()
  })
}

// ── NASM 컴파일 ──
async function compileAssembly(asm) {
  try {
    const bin = assemble(asm)
    console.log(`[asm.js] assembled ${bin.length} bytes`)
    return bin
  } catch (e) {
    console.error('[asm.js error]', e.message)
    // fallback to nasm if mini assembler fails
    try {
      const fs = require('fs')
      const { execSync } = require('child_process')
      fs.writeFileSync('/tmp/poke_gen.asm', asm)
      execSync('nasm -f bin -o /tmp/poke_gen.bin /tmp/poke_gen.asm 2>&1')
      console.log('[asm.js] fallback to nasm')
      return fs.readFileSync('/tmp/poke_gen.bin')
    } catch (e2) {
      console.error('[nasm fallback error]', e2.message)
      return null
    }
  }
}

// ── POKE 노드에 코드 전송 ──
function pokeNode(endpoint, machineCode) {
  return new Promise((resolve, reject) => {
    const url = new URL('/poke', endpoint)
    const options = {
      hostname: url.hostname,
      port: url.port,
      path: '/poke',
      method: 'POST',
      headers: {
        'Content-Type': 'application/octet-stream',
        'Content-Length': machineCode.length,
      },
      timeout: 10000,
    }

    const req = http.request(options, (res) => {
      let data = ''
      res.on('data', chunk => data += chunk)
      res.on('end', () => resolve(data))
    })
    req.on('error', e => resolve(`error: ${e.message}`))
    req.on('timeout', () => { req.destroy(); resolve('timeout') })
    req.write(machineCode)
    req.end()
  })
}

// ── Health Check + 리소스 모니터링 (5초마다) ──
const edgeHistory = new Map() // node_id → [{timestamp, data}]

setInterval(() => {
  for (const [id, node] of nodes) {
    if (node.endpoint.startsWith('polling:') || node.endpoint.startsWith('tcp:')) { node.status = 'alive'; node.last_seen = new Date().toISOString(); continue }
    const url = new URL('/health', node.endpoint)
    http.get(url.href, { timeout: 3000 }, (res) => {
      let data = ''
      res.on('data', chunk => data += chunk)
      res.on('end', () => {
        try {
          const health = JSON.parse(data)
          node.status = health.status || 'alive'
          node.last_seen = new Date().toISOString()
          node.health = health

          // 히스토리 저장 (최대 60개 = 5분)
          if (!edgeHistory.has(id)) edgeHistory.set(id, [])
          const history = edgeHistory.get(id)
          history.push({ timestamp: new Date().toISOString(), ...health })
          if (history.length > 60) history.shift()
        } catch {}
      })
    }).on('error', () => {
      node.status = 'dead'
      node.health = null
    }).on('timeout', function() { this.destroy(); node.status = 'dead' })
  }
}, 5000)

// ── Start ──
const PORT = 3333
server.listen(PORT, () => {
  console.log(`\n  POKE Hub running on http://localhost:${PORT}`)
  console.log(`  Endpoints:`)
  console.log(`    POST /enroll  — register a node`)
  console.log(`    GET  /nodes   — list nodes`)
  console.log(`    POST /run     — natural language → execute on node`)
  console.log(`    POST /poke-raw — send hex bytes to node`)
  console.log()
})

// ── HTTPS (for mobile voice — Web Speech API requires HTTPS) ──
// Activate with: HTTPS=1 node hub.js
if (process.env.HTTPS === '1') {
  try {
    const https = require('https')
    const fs = require('fs')
    const keyPath = __dirname + '/key.pem'
    const certPath = __dirname + '/cert.pem'
    if (fs.existsSync(keyPath) && fs.existsSync(certPath)) {
      const HTTPS_PORT = 3334
      https.createServer({
        key: fs.readFileSync(keyPath),
        cert: fs.readFileSync(certPath),
      }, handleRequest).listen(HTTPS_PORT, '0.0.0.0', () => {
        console.log(`  POKE Hub HTTPS on https://0.0.0.0:${HTTPS_PORT} (for mobile voice)`)
      })
    }
  } catch (e) { console.log('[HTTPS] skip:', e.message) }
}
