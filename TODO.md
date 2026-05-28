# POKE — Roadmap & Checklist

## Done

### Interactive Devices
- [x] Phase 1: Display — VBE 640x480, pixel art, flower rendering
- [x] Phase 2: Audio — PC speaker, 440Hz beep, Do-Re-Mi melody
- [x] Phase 3: Input — Keyboard ring buffer, scancode reading via /key

### Core
- [x] x86 bare-metal kernel (boot → TCP/IP → HTTP → code exec)
- [x] ARM64 bare-metal kernel (UART + serial protocol)
- [x] Hub LLM agent loop (tool_use, multi-step reasoning)
- [x] Built-in x86 assembler (asm.js, 300 lines, zero deps)
- [x] Built-in ARM64 assembler (asm_arm.js, zero deps)
- [x] Device profiles → auto-generated agent tools (14 profiles, 22+ ops)
- [x] Three-layer guard rail (asm.js + hub + kernel)
- [x] Voice pipeline (STT → LLM → execute → TTS)
- [x] Multi-edge orchestration + parallel execution (1.99x speedup)
- [x] Distributed computing (parallel_execute + load balancing)
- [x] Virtual sensors (PIT-based)
- [x] Docker one-command setup
- [x] 126 automated tests + CI
- [x] Open source launch (GitHub + Medium + Reddit)

---

## In Progress

### Infrastructure
- [x] CI 강화: QEMU 부팅 테스트, Docker 빌드 테스트 in GitHub Actions
- [x] npm 패키지 배포: `npx @orvian/poke`

### Protocol
- [x] 엣지 간 P2P 통신 (peer_execute: hub가 컴파일 + 릴레이)
- [x] 바이너리 스트리밍 (stream_animation: FRM 프로토콜, 연속 프레임)

### AI
- [ ] 멀티모달: 카메라/이미지 입력 → LLM 분석 → 하드웨어 반응
- [ ] 자율 에이전트: 배치된 바이너리가 조건 판단 + 허브 보고
- [ ] 프로파일 자동 생성: 미지 디바이스 → LLM 탐침 → 프로파일 JSON 자동 생성

---

## Backlog

### Interactive Devices
- [ ] Phase 4: 화면 버튼 + 키 감지 + 소리 피드백 조합
- [ ] 마우스/터치 입력 (PS/2 또는 USB HID)

### Infrastructure
- [ ] 웹 대시보드 (엣지 상태 실시간 모니터링 UI)

### Protocol
- [ ] 엣지→허브 POST (센서 자율 보고)

### Hardware
- [ ] 라즈베리파이 포팅 (GPIO LED 제어)
- [ ] ESP32 포팅 (WiFi 내장, $3)
- [ ] 실제 센서 연동 (I2C/SPI 온도센서)

### Ecosystem
- [ ] 프로파일 마켓플레이스 (웹 UI로 검색/공유)
- [ ] 튜토리얼 시리즈 ("POKE로 LED 켜기" 등)
- [ ] 플러그인 시스템 (커뮤니티가 도구 추가)
