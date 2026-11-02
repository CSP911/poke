# 제품팀 메모 (2026-10-11)

## 실행 기록 (캐시된 문장만, 부팅 2회, 당시 다른 QEMU 0개)
1. `say.py --show "온도가 25도 넘으면 LED 0번을 켜고, 아니면 꺼줘"` → op from cache, 8416 B, check passed,
   result {"temp_c": 23.5, "led0": "off", "threshold_c": 25}, 켜짐→꺼짐 29 ms, total 0.4 s.
   truth 23750 mC인데 결과는 23.5 — CLI는 차이 설명 없음(센서 해상도 0.5는 보고 HTML에만 있음).
   보고 HTML의 터미널 전사문 SAY 문장("Temperature is 25 C or below, so LED 0 is now off.")이 실제 출력과 다름.
2. `say.py "자기장 세기 알려줘"` (기본 실험대) → {"mag_x":0,"mag_y":0,"mag_z":0,"field_strength_counts":0},
   ground truth 줄에는 자기장이 없음 → 사용자가 맞는지 판단할 근거 0.

## 코드에서 확인한 것
- say.run(): 캐시 경로에서 check 결과가 REFUSED여도 부팅을 막지 않음(출력만 'REFUSED'). README의 "refused images never boot"는 새 op에만 참.
- 캐시 키 = sha1(문장 원문 + catalog JSON). "온도 알려줘 "(공백), "온도알려줘", "LED 4번 켜줘"는 전부 새 claude -p. 카탈로그를 한 글자 바꾸면 캐시 전체 무효.
- 실패 경로(claude 로그인 없음, qemu 없음, --pi 어댑터 없음, 3라운드 실패)는 파이썬 traceback으로 끝남.
- 결과 키가 문장마다 모델이 지은 이름(temperature_c / temp_c) — 고정 스키마 없음.
- POKE-SAY는 ASCII 영어만(한국어 질문 → 영어 서술).
- 실기기(--pi)는 truth가 {} — 트윈 밖에서는 대조 근거가 사라짐.
- catalog.json 위생: tmp105 use_when에 "0x4a", led from_driver "i2c-led-61"인데 default_addr 0x60.
- build/img/에 모든 .img/.elf가 남음(노드는 0 B지만 허브는 전부 보관), op 캐시는 build/ 아래(gitignore) — "명시적 기억"이 아니라 빌드 부산물.
