# 시험 지도 — 무엇이 바뀌어도 지켜야 하나(I), 무엇이 바뀌는가(T)

POKE는 "바뀌는 것"이 본질이다. 노드는 요청마다 다른 이미지로 부팅되고, 몸은 부품이 옮겨지고,
같은 노드가 낮에는 검사를, 밤에는 관측을 맡는다. 그래서 시험을 두 축으로 나눈다.

- **I (불변식)**: 어떤 변환이 일어나도 깨지면 안 되는 것.
- **T (변환)**: 무엇이 바뀌는지에 따라 나눈다. 각 시험은 바뀌기 전과 후, 그리고 그 사이의 경계를 본다.

`regress.py`의 모든 케이스는 정확히 한 범주에 속한다(`CATEGORY_RULES`).

    ../sim/body/.venv/bin/python regress.py --list --cats       # 범주와 케이스 수
    ../sim/body/.venv/bin/python regress.py --list --by-cat     # 케이스를 범주별로
    ../sim/body/.venv/bin/python regress.py --quick --cat T2    # 한 범주만

## I · 불변식

| 범주 | 지키는 것 | 주요 스위트·시험 |
|---|---|---|
| I1 격리·안전 | op가 노드나 방을 해치지 못함 (EL0, 허용 목록, 값 범위, 계산 주소, 무한 루프). 허브가 서명한 이미지만 켜짐(넷부팅, Pi 서명 부팅 형식) | fault, fence, boottest, check-fp, checktest, policy/refused-*, verify/t_r3·t_v6·t_v7, **sign/t_signed_boot** (S1–S8 통과, S9 재전송 XFAIL) |
| I2 휘발 | 끝나면 노드에 우리 코드나 데이터가 0 B | policy/op-region-zero 외, hw/t_power |
| I3 보고 | 결과와 메시지 형식, 허브가 결과를 그대로 믿어도 됨 | result, hub/parse, message, time |
| I4 허브 견고성 | 동시 요청, QEMU·파일 누수, 기록 경합, 소유자 기록 보호 | hub, keylock, guard |
| I5 사람 경계 | 쓰기 확인, 범위 밖 거절, 모호하면 되물음, 정책 거부 | confirm, prefilter, verify/t_x2 |
| I6 부품 진실 | 드라이버 조각이 실험대 진실과 일치 | selftest, frag (+ spec_measure.py, regress 밖) |

## T · 변환

모든 변환 시험은 같은 다섯 가지를 묻는다. 새 시험도 이 틀로 쓴다.

1. **전**: 바뀌기 전의 역할이나 몸이 옳게 동작하는가
2. **후**: 바뀐 뒤의 역할이나 몸이 옳게 동작하는가
3. **잔재 없음**: 남지 않아야 할 것이 남지 않는가 (노드 메모리, 이전 역할의 권한, 맞지 않게 된 캐시 op)
4. **이어짐**: 이어져야 할 것이 이어지는가 (허브 기록, 세계 상태, 같은 역할이면 같은 바이트)
5. **경계**: 전환 중에 들어온 요청이나 전환 실패가 안전한 기본값으로 떨어지는가

| 범주 | 무엇이 바뀌나 | 지금 있는 시험 | 비어 있는 곳 |
|---|---|---|---|
| T1 목적 | 같은 몸에 다른 문장이나 계획 | sentence, effects, record, demo, fresh, registry/t_registry | 여러 노드 계획의 모델 없는 회귀 |
| **T2 시간·역할** | 같은 몸이 시간에 따라 역할을 교대 (낮 불량 검사 / 밤 설비 관측), 예약, 상시 의도 | **role/t_role_shift** (R1–R4 통과, R5–R7 XFAIL), schedule(full), watch | 아래 R5–R7 |
| T3 몸 | 부품이 옮겨지거나, 빠지거나, 새로 붙거나, 학습됨 | absent, catalog, trace, hw/t_body_change·t_newparts·t_learn, verify/t_r2·t_x3 | 역할 핀 배선이 바뀔 때(L1 역할 재지정) |
| T4 기체·경로 | 같은 문장이 다른 아키텍처나 부팅 경로로 감 | netboot(full), x86/selftest(full) | quick에 없음, ESP32-C3 회귀 |
| T5 세계 | 노드 밖 환경이 바뀜 | registry/t_world | 세계 규칙과 상시 의도 결합(모델 없는 판) |

### T2 시간·역할 — `teams/role/t_role_shift.py`

이 시험은 공장 현장(`POKE_SITE=factory`, `registry/sites/factory/nodes/line1.json`)에서 돈다. 노드 하나에 부품이 셋 붙어 있다.

- 검사 지그 전원선: ADM1272
- 설비 온도: EMC1413 (제어함, 주축 모터 베어링, 감속기)
- 입출력 보드: PCA9554 (불량 배출 게이트, 제품 감지)

| 역할 | 허용 능력 | 하는 일 |
|---|---|---|
| 낮 · 검사 | power.measure, motion.read@item, switch.set@reject_gate | 제품의 통전 전류가 1.5–2.5 A 밖이면 게이트로 배출 |
| 밤 · 관측 | temperature.read ×3, power.measure | 읽기만 한다. 게이트는 이 몸에 없음 |

| 케이스 | 다섯 질문 중 | 상태 |
|---|---|---|
| R1 낮 판정 (양품, 단락, 빈 지그 → 게이트 핀 진실) | 전 | PASS |
| R2 밤에는 게이트가 컴파일되지 않고, 허용 목록에 쓰기가 0 | 후·잔재 없음(권한) | PASS |
| R3 낮 다음 밤: 두 부팅 모두 메모리 0, 이미지가 서로 다름, 밤은 설비를 읽음 | 잔재 없음·후 | PASS |
| R4 내일의 낮 이미지 = 오늘의 낮 이미지 (바이트 동일) | 이어짐 | PASS |
| R5 허브가 시계로 역할을 고름 (노드의 `modes`: 시간대 → 허용 능력) | 경계 | XFAIL: 기능 없음 |
| R6 19:59에 시작해 20:00을 넘는 요청 (낮 권한으로 끝낼지, 거절할지) | 경계 | XFAIL: 규칙 미정 |
| R7 역할별 기준선 (밤 추이에 낮 생산 열이 섞이지 않게) | 이어짐 | XFAIL: 기록 분리 없음 |

R5–R7을 통과시키는 일은 이제 시험이 정해 둔 기능 작업이다(registry의 역할 일정, 허브의 경계 규칙, 역할별 기록).

## P · 성능·변동·내구 (regress 밖, 합격/불합격보다 분포를 봄)

- teams/test/e1_eval: 처음 듣는 문장 정확도
- teams/test/e2_intent: 같은 뜻 매칭
- teams/test/e3_load, e3_ui: 부하
- teams/test/e9_variance: 생성 변동
- teams/test/e11_soak: 지구력
- teams/test/e12_trace: 흔적 대조
- hw/t_reliability: 학습 신뢰도
- product/demo_check: 브라우저 데모
- hw/t_netboot_order: 실기기 부팅 순서
- spec_measure.py: 능력 사양 실측

## 규칙

- 소유자 기록을 건드리지 않는다. 기록은 POKE_RECORDS, POKE_INCUBATED, POKE_WORLD를 임시 경로로 두고 쓴다. guard가 검사한다.
- `--quick`은 모델을 호출하지 않는다. op는 기록이나 손으로 쓴 C를 쓴다.
- 새 케이스는 `CATEGORY_RULES`에 범주가 잡혀야 한다(`--list`에서 `?`가 나오면 안 됨).
- 아직 없는 기능은 XFAIL로 먼저 적어 둔다. 통과하기 시작하면 XPASS로 알려 준다.
