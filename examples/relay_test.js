/**
 * 엣지(폰)가 허브에게 명령하는 테스트
 * node relay_test.js "거실TV에 날씨 보여줘"
 */
const http = require('http')

const command = process.argv[2] || 'show current time on qemu-01'
const from = process.argv[3] || 'phone-01'

console.log(`[${from}] → Hub: "${command}"`)

const body = JSON.stringify({ from, command })
const req = http.request({
  hostname: 'localhost', port: 3333, path: '/relay', method: 'POST',
  headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
  timeout: 30000,
}, (res) => {
  let data = ''
  res.on('data', c => data += c)
  res.on('end', () => {
    try {
      const r = JSON.parse(data)
      console.log(`[Hub] plan: ${r.plan.type} → ${r.plan.target}`)
      console.log(`[Hub] task: "${r.plan.task}"`)
      console.log(`[Hub] result: ${typeof r.result === 'string' ? r.result : JSON.stringify(r.result)}`)
    } catch {
      console.log('Response:', data)
    }
  })
})
req.on('error', e => console.error('Error:', e.message))
req.write(body)
req.end()
