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
- [x] 멀티모달: analyze_image 도구 + /camera 엔드포인트 (Claude Vision API)
- [x] 자율 에이전트: deploy_autonomous + list_monitors + stop_monitor (주기적 재실행)
- [x] 프로파일 자동 생성: auto_profile (PCI 스캔 → LLM 프로브 → 프로파일 저장 → 자동 리로드)

---

## Next Leap (기술적 도약)
- [ ] 브라우저 베어메탈: WebAssembly로 POKE 엣지를 브라우저에서 실행 (설치 0)
- [ ] 베어메탈 LLM 추론: 엣지에 OS 없이 LLM 직접 실행 (행렬 곱 어셈블리)
- [ ] 자기 수정 커널: LLM이 런타임에 커널 자체를 업그레이드
- [ ] 크로스 아키텍처 마이그레이션: x86 → ARM 실행 중 이전 (LLM이 레지스터 변환)
- [ ] POKE 메시 네트워크: 엣지 자동 발견 + 허브 없이 협력

---

## Next: Mobile Commander
- [ ] PWA: mobile.html → Progressive Web App (홈 화면 추가, 오프라인 캐시, 앱 느낌)
- [ ] 스마트폰 커맨더: 음성 + 텍스트 + 카메라로 모든 엣지 제어
- [ ] 클라우드 허브: 허브를 클라우드에 배포 (폰 → 인터넷 → 허브 → 엣지)

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
