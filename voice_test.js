/**
 * POKE Voice Pipeline Test
 *
 * 시뮬레이션 흐름:
 * 1. macOS say → 한국어 음성 생성 (WAV)
 * 2. WAV를 ARM QEMU에 UART로 전송 (마이크 입력 모사)
 * 3. 동시에 허브 /voice로 전송 → STT → LLM → 계산
 * 4. 허브가 TTS 응답 생성 → ARM에 TONE으로 전달
 */

const { execSync } = require('child_process')
const fs = require('fs')
const net = require('net')
const http = require('http')

const HUB = 'http://localhost:3333'
const ARM_TCP = { host: 'localhost', port: 8081 }

// ── 1. 음성 생성 ──
const text = process.argv[2] || '2 더하기 3 계산해줘'
console.log(`\n🎤 음성 생성: "${text}"`)

execSync(`say -v Yuna -o /tmp/poke-voice.aiff "${text}"`)
execSync(`afconvert -f WAVE -d LEI16@8000 -c 1 /tmp/poke-voice.aiff /tmp/poke-voice.wav`)

const wavData = fs.readFileSync('/tmp/poke-voice.wav')
console.log(`   WAV: ${wavData.length} bytes (8kHz, 16-bit, mono)`)

// ── 2. ARM에 음성 전송 (마이크 입력 시뮬레이션) ──
console.log(`\n📟 ARM QEMU로 음성 전송 (TCP:${ARM_TCP.port})...`)

const armSock = new net.Socket()
let armSent = false

armSock.connect(ARM_TCP.port, ARM_TCP.host, () => {
  // POKE protocol: "POKE" + len(4 LE) + "TONE" + freq(2) + dur(2)
  // 마이크 데이터를 ARM에 보내는 대신, ARM에 "소리가 들어왔다"는 신호를 보냄
  const cmd = Buffer.from('VOIC')  // 새 명령: VOICE data incoming
  const payload = Buffer.concat([cmd, wavData.slice(44)])  // WAV 헤더 스킵, raw PCM만

  const header = Buffer.alloc(8)
  header.write('POKE', 0)
  header.writeUInt32LE(payload.length, 4)

  armSock.write(Buffer.concat([header, payload]))
  armSent = true
  console.log(`   → ARM에 ${payload.length} bytes 전송 완료`)

  // ARM 응답 대기
  armSock.once('data', (data) => {
    console.log(`   ← ARM 응답: ${data.toString().replace(/[^\x20-\x7E]/g, '.')}`)
    armSock.destroy()
  })

  setTimeout(() => armSock.destroy(), 3000)
})

armSock.on('error', (e) => {
  console.log(`   ⚠ ARM 연결 실패: ${e.message} (건너뜀)`)
})

// ── 3. 허브에 음성 전송 → STT → 처리 ──
console.log(`\n🧠 허브에 음성 전송 → STT → LLM → 계산...`)

const voiceReq = http.request(
  `${HUB}/voice?from=arm-qemu&hint=${encodeURIComponent(text)}`,
  { method: 'POST', headers: { 'Content-Type': 'audio/wav' } },
  (res) => {
    let body = ''
    res.on('data', c => body += c)
    res.on('end', () => {
      try {
        const result = JSON.parse(body)
        console.log(`\n✅ 결과:`)
        console.log(`   📝 인식된 텍스트: "${result.transcript}"`)
        console.log(`   📋 계획: ${result.plan?.type} → ${result.plan?.target}`)
        console.log(`   🔢 실행 결과: ${result.result}`)
        console.log(`   💬 응답: "${result.reply_text}"`)
        console.log(`   🔊 응답 오디오: ${result.reply_audio_size} bytes`)

        // ── 4. 응답 오디오를 ARM에 전송 ──
        if (result.reply_audio_base64) {
          const replyPcm = Buffer.from(result.reply_audio_base64, 'base64')
          console.log(`\n📟 ARM에 응답 오디오 전송 (${replyPcm.length} bytes)...`)

          // TONE 명령으로 간단한 확인음 전송
          const toneSock = new net.Socket()
          toneSock.connect(ARM_TCP.port, ARM_TCP.host, () => {
            // 성공음: 880Hz, 200ms
            const tonePayload = Buffer.alloc(8)
            tonePayload.write('TONE', 0)
            tonePayload.writeUInt16LE(880, 4)
            tonePayload.writeUInt16LE(200, 6)

            const toneHeader = Buffer.alloc(8)
            toneHeader.write('POKE', 0)
            toneHeader.writeUInt32LE(8, 4)

            toneSock.write(Buffer.concat([toneHeader, tonePayload]))
            console.log(`   → ARM에 확인음 전송 (880Hz, 200ms)`)

            toneSock.once('data', (data) => {
              console.log(`   ← ARM: ${data.toString().replace(/[^\x20-\x7E]/g, '.')}`)
              toneSock.destroy()
            })
            setTimeout(() => toneSock.destroy(), 2000)
          })
          toneSock.on('error', () => {})

          // 로컬에서 응답 오디오 재생
          console.log(`\n🔊 응답 재생: "${result.reply_text}"`)
          try {
            execSync(`afplay /tmp/poke-voice-reply.wav`)
          } catch (e) {
            execSync(`say -v Yuna "${result.reply_text}"`)
          }
        }

        console.log(`\n────────────────────────────────`)
        console.log(`📞 전체 파이프라인 완료!`)
        console.log(`   🎤 "${text}"`)
        console.log(`   → 📟 ARM (마이크)`)
        console.log(`   → 🧠 허브 (STT + LLM)`)
        console.log(`   → 🖥  ${result.plan?.target} (실행)`)
        console.log(`   → 🔊 ARM (스피커)`)
        console.log(`────────────────────────────────\n`)

      } catch (e) {
        console.error('파싱 실패:', body)
      }
    })
  }
)

voiceReq.on('error', (e) => console.error('허브 연결 실패:', e.message))
voiceReq.write(wavData)
voiceReq.end()
