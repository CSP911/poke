/**
 * 오늘 날씨를 가져와서 → LLM이 시각화 코드 생성 → 엣지에 렌더링
 * node weather.js
 */
const http = require('http')
const https = require('https')

const HUB = 'http://localhost:3333'

// 1. 날씨 가져오기 (wttr.in — 무료, API 키 불필요)
function getWeather() {
  return new Promise((resolve, reject) => {
    https.get('https://wttr.in/Seoul?format=j1', (res) => {
      let data = ''
      res.on('data', c => data += c)
      res.on('end', () => {
        try {
          const w = JSON.parse(data)
          const cur = w.current_condition[0]
          resolve({
            city: 'Seoul',
            temp: cur.temp_C,
            feels: cur.FeelsLikeC,
            humidity: cur.humidity,
            desc: cur.weatherDesc[0].value,
            wind: cur.windspeedKmph,
            cloud: cur.cloudcover,
          })
        } catch (e) { reject(e) }
      })
    }).on('error', reject)
  })
}

// 2. LLM에게 날씨 시각화를 요청 (허브의 /draw 사용)
function drawOnEdge(task) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ task })
    const req = http.request({
      hostname: 'localhost', port: 3333, path: '/draw', method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
      timeout: 30000,
    }, (res) => {
      let data = ''
      res.on('data', c => data += c)
      res.on('end', () => resolve(data))
    })
    req.on('error', reject)
    req.write(body)
    req.end()
  })
}

async function main() {
  console.log('Fetching weather...')
  const w = await getWeather()
  console.log(`Weather: ${w.city} ${w.temp}°C, ${w.desc}, humidity ${w.humidity}%`)

  const prompt = `Draw a weather display for Seoul on a 100x100 canvas with dark background.
Current conditions:
- Temperature: ${w.temp}°C (feels like ${w.feels}°C)
- Weather: ${w.desc}
- Humidity: ${w.humidity}%
- Wind: ${w.wind} km/h
- Cloud cover: ${w.cloud}%

Draw a visual scene that represents this weather:
- If sunny: bright yellow sun, blue sky
- If cloudy: gray clouds
- If rainy: clouds with rain drops
- If clear: stars and moon

Also draw the temperature "${w.temp}°" as large pixel text in the center.
Use simple pixel art style. Make it colorful and clear.`

  console.log('Sending to hub (LLM → edge)...')
  const result = await drawOnEdge(prompt)
  console.log('Result:', result)
}

main().catch(console.error)
