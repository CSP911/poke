# 8시간 작업 계획 (2026-10-11 밤 → 아침)

소유자 지시: 실기기는 나중에. 지금은 제품 완성도. 소유자 컨펌 없이 팀 판단으로 결정하고, 시행착오를 겪으며 진행한다. 커밋은 하지 않는다.
의장(메인 세션)이 통합·회귀·최종 보고를 맡는다. 되돌리기용 백업: 스크래치패드 checkpoints/cp0-before-8h.tgz.

## 의장이 이미 한 것 (H0)
- `say.boot()`: 부팅마다 시리얼 파일을 따로 씀(`build/serial/<pid>-<port>.txt`) → **트윈 병렬 부팅 안전**. 부팅 잠금 불필요. 4대 동시 부팅 확인.
- QEMU는 어떤 경우에도 호출 밖으로 살아남지 않음(try/finally) — D5.
- `parse_result()`: 결과 줄이 깨져도 예외 없음, `result_error`로 보고 — D2 허브 쪽.
- 꺼짐 판정 = POKE-OFF 줄 **그리고** VM 정지. 줄만 있으면 `warning` — D4 허브 쪽.

## 파일 소유 (충돌 방지 규칙)
| 파일 | 소유 | 비고 |
|---|---|---|
| `edge/sentence/` boot.S, rt.c, rt.h, pt.h, ustub.S, image.ld, i2c_bus.h | 검사기 | 다른 팀의 런타임 변경은 새 파일로 쓰고 훅은 검사기가 건다 |
| `edge/sentence/rt_power.c/.h` (신규) | 실기기 | FIFO 비우기, PM_RSTS, 워치독+RCFG, 예산 초과 종료, 리셋 전 0 채우기 |
| `say.py`: build(), check(), OP_WINDOWS, RUNTIME_API, make_op의 거절 경로 | 검사기 | |
| `say.py`: run(), CLI(`__main__`), OP_SYSTEM, op_prompt, make_op의 캐시 경로 | 제품 | 기능 본체는 새 모듈에 |
| `say.py`: boot(), parse_result(), boot_pi(), boot_netboot_twin() | 의장 | 바꿀 일이 있으면 의장에게 요청 |
| `sentence/record.py, messages_ko.py, schedule.py, ui/` (신규) | 제품 | |
| `sentence/rtcfg.py` (신규), `netboot/**` | 실기기 | |
| `sentence/regress.py` (신규), `teams/test/**`, `checktest.py`는 검사기와 공유 | 시험 | |
| `frags/*.h`, `fragments.py` | 검사기 (인터페이스 불변) | |

**say.py 규칙**: 자기 구역만 고친다. 고치기 직전에 반드시 다시 읽는다. 함수 시그니처 `boot(path, bench, budget_s, script)`, `build(src, tag)`, `check(obj)`, `make_op(sentence)`, `run(sentence, ...)`는 깨지 않는다(키워드 인자 추가는 가능).

## 공유 신호: `sentence/teams/STATUS.md`
각 팀은 단위가 끝날 때마다 한 줄을 덧붙인다: `HH:MM 팀 단위 상태 — 한 줄 요약 (regress --quick 결과)`.
기다리는 신호가 있으면 이 파일을 확인한다. 주요 신호:
- `V1-MERGED` (검사기): v1 런타임이 본선. 이후 실기기 훅, 제품의 say.py 통합 시작 가능.
- `MANIFEST-FORMAT` (제품): manifest 형식 확정(검사기 단위 5의 입력).
- `REGRESS-READY` (시험): `regress.py --quick` 사용 가능.
- `RTCFG-READY` (실기기): 이미지 설정 블록 형식과 `rtcfg.py` 사용 가능.

## 일정
| 시간 | 시험 | 검사기 | 실기기 | 제품 |
|---|---|---|---|---|
| 0–1.5 | T1 regress.py, 묶음 이전, xfail 표시 → REGRESS-READY | V1 v1 본선 통합 + 거부 이미지 부팅 금지 + v0 주소 규칙 경고화 + FAULT 원인을 LLM 피드백으로 → V1-MERGED | H1 보관형 tftpd 본선 / H2 vboot 실제 요청 순서 | P0 manifest 형식 확정 → MANIFEST-FORMAT. P1 record.py·P3 messages_ko.py 새 모듈로 (say.py 아직 손대지 않음) |
| 1.5–4 | T3–T5 정책·기록·확인 시험(xfail 먼저) | V2 delay_ms 초과는 명시적 fail. V4 GPIO 창 제거, I2C 핀 설정을 런타임으로 | H3 rt_power.c + rtcfg.py → RTCFG-READY, 훅 3줄은 검사기가 적용 | P1·P2·P3를 say.py에 통합(record 캐시, manifest·확인·NEED, 한국어 메시지, `--yes`) |
| 4–6.5 | T6 2시간마다 --full, 깨진 것은 원인 팀에 | V5 I2C를 런타임 호출로, manifest 허용 목록 강제, CDIV는 RTCFG 값 사용 | H4 CDIV 대상별 계산(rtcfg) / H5 preflight.py | P4 schedule.py 예약 부팅. P5 데모 화면 ui/ |
| 6.5–8 | T7 데모 시나리오를 시험으로, 최종 --full | 여유·회귀 | H6 README 정정, 여유 | P6 아침 안내, 써볼 문장 10개 |

의장: 각 신호 시점과 2시간마다 `regress.py --full`, 최종 통합 확인, 결과 보고 페이지, 메모리 갱신.

## 공통 규칙
- 본선을 고친 단위는 `regress.py --quick`이 통과해야 완료. 새로 깨진 것은 30분 안에 고치거나 되돌린다.
- 트윈 부팅은 병렬 가능. 단, 시간 측정 시험은 부하에 흔들릴 수 있으니 시간 필드로 판정하지 않는다.
- op 생성(claude -p)은 필요한 만큼만. ANTHROPIC_API_KEY는 claude_cli가 환경에서 뺀다. 키 파일을 읽거나 출력하지 않는다.
- 검사기를 일부러 속이는 코드를 새로 작성하지 않는다. 격리 시험은 이미 있는 사례(teams/verify/edge/tests, teams/test)를 쓴다.
- git 커밋·푸시 금지. 막히면 판단해서 우회하고, 그 판단을 STATUS.md에 한 줄로 남긴다.

---
# 중간 점검 회의 (22:45) — 남은 시간 계획

## 지금까지 (1차~3차, 약 1시간 15분)
v1 격리 본선 + op 하드웨어 창 0개 + I2C 허용 목록 · 거부 이미지 부팅 금지 · 행동 기록(POKE-EFFECTS) · 리셋 뒤 메모리 실측 0 · 문장 기록·같은 뜻·파라미터 · 확인·되물음·한국어 · 예약 부팅 · 데모 화면(음성) · 카드·다른 몸 재발음 · 새 장치 4개 무인 인큐베이팅(TMP421·PCA9554·SSD0303·DPS310) · 몸 변화 감지(옮김·빠짐·새 장치) · 실기기 준비(보관형 tftpd·실제 요청 순서·워치독·preflight) · regress 140건.

## 회의 결정
1. **상시 의도는 안전 상한을 전제로만 한다** (제품 제안 + 검사기 반대 → 조건부 채택): 감시 op는 읽기 전용만, 행동은 조건 충족 시 별도 확인, FAULT 연속 2회면 자동 중지, 하루 부팅 상한, 감시 목록은 화면에서 보이고 멈출 수 있어야 한다.
2. **금지·범위 문장은 op를 쓰기 전에 허브가 거른다** (시험 제안, 검사기 찬성 — 최종 강제는 여전히 런타임).
3. **make_op에 키별 잠금(say.key_lock)을 넣는다** (R4 — 의장 결정: 넣는다. 같은 문장 동시 생성 시 모델 1회).
4. **값 범위·쓰기 횟수를 런타임이 강제한다** (검사기 1순위, 서보·히터 대비).
5. **카탈로그 = 지금 몸에 실제로 있는 부품** (실기기 1순위) → 그 위에 **"배워줘" 학습 고리**를 허브·화면에서 닫는다. 학습 결과는 **검사기 안전 심사**(관측된 쓰기 ⊇ 선언, 최소 권한, identify 신뢰도 < 0.8이면 쓰기 deny)를 통과해야 등록.
6. **흔적 대조 재발음**: 다시 쓴 op의 첫 결과·EFFECTS를 카드의 흔적과 대조해 채택/표시/거부 (더늠의 핵심 반론에 대한 답).
7. **시험은 소유자 기록을 절대 건드리지 않는다**: 모든 팀 시험 스크립트가 시작 때 POKE_RECORDS를 임시 디렉터리로. regress는 실행 전후 소유자 기록 불변 확인.
8. **5분 데모 모드**: 마지막 물결. 데모 중 정책은 읽기·LED만, 자동 학습·'항상' 승인 꺼짐.

## 4차 물결 (22:45 → 약 01:30)
| 팀 | 단위 |
|---|---|
| 제품 | D1 금지·범위 사전 거름 · D2 make_op key_lock · D3 상시 의도(결정 1의 상한 — 상한 규격은 검사기와 STATUS로) |
| 검사기 | V6 값 범위·쓰기 횟수 런타임 강제(manifest targets와 형식은 제품과) · V7 인큐베이팅 결과 안전 심사(실기기와) · 상시 의도 상한 규격 제공 |
| 실기기 | H7 카탈로그를 실제 몸 기준으로(기본 실험대 카탈로그·캐시 키는 불변) · H8 incubation·bodycheck 원자적 쓰기(say.write_json_atomic, 잠금) · H9 identify() 의무화 · H10 학습 작업 API(백그라운드, 진행 이벤트) |
| 시험 | T8 소유자 기록 보호(결정 7) · T9 생성 변동 측정(쓰기·조건·반복 12문장 × 3회, EFFECTS 집합 비교) · T10 새 기능 회귀 편입 |

## 5차 물결 (약 01:30 → 04:30)
| 팀 | 단위 |
|---|---|
| 제품 | 흔적 대조 재발음 · "배워줘" 화면 고리(실기기 H10 위에) · 5분 데모 모드 |
| 실기기 | 인큐베이팅 신뢰도(장치당 3회, 격리 사본) + 음성 대조군 |
| 검사기 | 상시 의도·학습 고리 감사, AUDIT 갱신 |
| 시험 | 1시간 지구력(데모 서버+예약+넷부팅 트윈+regress 동시) · 흔적 대조 평가 · 최종 --full |

## 마무리 (04:30 → 05:30) — 의장
최종 회귀, README/MORNING 정합성, 아침 보고 페이지, 메모리 갱신.
