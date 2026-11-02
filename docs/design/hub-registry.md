# 허브의 몸 기록부 — 여러 Edge, 인큐베이팅의 추상화, LLM과 컴파일러로 가는 길 (초안 v0, 2026-11-02)

## 0. 왜 필요한가

지금 허브는 "몸이 하나"라고 가정한다. 장치 정보는 여섯 곳에 흩어져 있고(`bench.py`, `wiring.json`,
`frags/catalog.json`, `manifest.FN` + `incubated/fn.json`, `incubated/<부품>.json`, RouteMind 지도), 모델은
`tmp105_read_mC(0x48)` 같은 **칩 이름의 함수**를 받아 op를 쓴다. 이 구조로는 세 가지가 안 된다.

1. 노드가 여러 대일 때 "어느 노드의 무엇을 쓸 수 있나"를 말할 수 없다.
2. 같은 일을 하는 다른 칩으로 바뀌면 op를 다시 써야 한다(지금의 재발음).
3. 모델이 칩 세부를 알아야 해서, 부품이 늘수록 프롬프트가 커지고 실수가 는다.

## 1. 다섯 계층

```
L4  계획 · op          목적 → 노드별 역할 → 노드별 C 소스          (LLM이 쓰는 곳)
L3  능력(Capability)    "온도를 잰다", "LED를 켠다" — 칩과 무관한 인터페이스 + 실측 사양   (LLM이 보는 곳)
L2  부품 형(PartType)   인큐베이팅 산출물: 식별, 레지스터, 조각(드라이버), 능력 구현, 권한
L1  부착(Attachment)    이 노드의 이 버스, 이 주소에 이 부품 형이 실제로 있다 (몸 지문의 원천)
L0  노드(Node)          한 대의 Edge: 아키텍처, 부팅 경로, 런타임 ABI, 버스, 연결, 반사 능력
```

원칙
- **LLM은 L3까지만 본다.** 칩 이름, 레지스터, 주소는 모델의 일이 아니다.
- **인큐베이팅은 L2를 만들고, L2를 L3 인터페이스에 묶는다.** 묶을 인터페이스가 없으면 새 인터페이스를 *제안*할 뿐 스스로 만들지 않는다(사람·검사기 심사).
- **권한의 원본은 L2의 정책이다.** L3/L4가 무엇을 요청하든 허용 목록은 L2에서 기계적으로 유도한다(지금의 fn.json 원칙 유지).
- **지도(RouteMind)는 L0–L3의 생성된 보기다.** 원본이 아니다.

## 2. 계층별 모양 (JSON)

### L0 노드
```json
{ "id": "living-pi4",
  "arch": "aarch64-pi4",                     // aarch64-pi4 | x86-pc | rv32-esp32c3 | …
  "boot": "pi-eeprom-tftp",                  // pi-eeprom-tftp | pxe | ipxe-resident | uboot-tftp | esp-rom-uart | seed
  "abi": "rt.h/1",                           // 이 노드의 런타임이 지키는 계약 버전
  "buses": ["i2c1"],
  "limits": { "run_budget_ms": 20000, "image_max_kb": 512, "simd": false },
  "reflex": ["stop-on-budget", "watchdog-15s"],   // 허브 없이 몸이 보장하는 것
  "link": { "hub": "10.0.0.1", "kind": "ethernet" },
  "place": "거실",                            // 사람이 붙인 이름 — 계획이 "거실"을 말할 수 있게
  "owner_policy": "home-default" }
```

### L1 부착 (인스턴스)
```json
{ "id": "living-pi4/i2c1/0x48",
  "node": "living-pi4", "bus": "i2c1", "addr": "0x48",
  "part": "tmp105",                          // L2 부품 형
  "seen": "2026-11-02T10:12:03Z",            // 마지막 스캔·identify 확인
  "how": "identify",                         // identify | signature | declared
  "label": "거실 창가 온도" }                  // 사람이 붙이는 이름(선택)
```

### L2 부품 형 (인큐베이팅 산출물)
```json
{ "id": "emc1403",
  "identity": "SMBus temperature sensor, internal + 2 remote diodes",
  "identify": { "fn": "emc1403_identify", "kind": "id-register" },     // id-register | layout | behaviour | none
  "verification": { "kind": "twin-knobs", "on": "lab", "rounds": 2, "when": "2026-10-13" },
  "fragment": "frags/emc1403.h",
  "implements": [                                                    // L3 인터페이스와의 묶음
    { "cap": "temperature.read", "channels": ["internal", "remote1", "remote2"],
      "via": "emc1403_read(addr, ch, *mc)",
      "spec": { "unit": "m°C", "range": [-64000, 191000], "resolution": 125, "accuracy": 1000, "latency_ms": 30 } } ],
  "functions": { "emc1403_read": { "access": "read", "policy": "allow", "write_regs": [], "max_write": 1 } },
  "safety": "inert",                                                 // inert | changes-room | physical-risk
  "real_chip_todo": null }
```
`spec`은 **실측**으로 채운다: 실험대 손잡이로 값을 넣고 읽어 범위·분해능·오차·지연을 잰다(능력 측정 인큐베이팅).
부품이 사양을 못 재는 경우(손잡이 없음)는 `"spec_basis": "datasheet"`로 표시하고 지도에도 그렇게 쓴다.

### L3 능력 인터페이스 (허브가 소유하는 작은 어휘)
```json
{ "cap": "temperature.read", "v": 1,
  "call": "int cap_temperature_read(cap_t c, long *milli_c)",       // op가 쓰는 C 모양
  "returns": "0 ok, <0 error",
  "describes": "온도를 한 번 읽는다 (m°C)",
  "safety": "inert" }
```
처음 어휘(v0, 지금 부품 12개가 쓰는 것): `temperature.read`, `pressure.read`, `magnetic.read`, `time.read`,
`time.set`(정책 deny), `switch.set`(LED·핀, changes-room), `switch.read`, `display.text`(심사로 deny 중),
`power.measure`(전압·전류·전력). 다음 후보: `ir.send`, `ir.learn`, `humidity.read`, `presence.read`.

### L4 계획 (모델이 내는 JSON)
```json
{ "purpose": "거실이 왜 저녁마다 더운지 찾아줘",
  "steps": [
    { "id": "s1", "on": "living-pi4", "uses": ["cap:living-pi4/i2c1/0x48#temperature.read"],
      "do": "17시부터 21시까지 10분마다 읽기", "kind": "watch", "every_s": 600 },
    { "id": "s2", "on": "window-esp32", "uses": ["cap:window-esp32/i2c1/0x4c#temperature.read@remote1"],
      "do": "같은 시각 창가 원격 채널 읽기", "kind": "watch", "every_s": 600 },
    { "id": "s3", "on": "hub", "after": ["s1", "s2"], "do": "두 곡선 비교, 일몰 시각과 대조", "kind": "analyze" } ],
  "writes": [],
  "why": { "living-pi4": "거실에 온도 센서가 있는 유일한 노드", "window-esp32": "창가 원격 다이오드" } }
```

## 3. LLM에게 가는 길 — 두 단계, tool_use 모양

**1단계 — 계획(무엇을, 어느 노드로).** 모델은 몸 전체를 받지 않고 *도구로 묻는다*.

| 도구 | 돌려주는 것 |
|---|---|
| `body_summary()` | 노드 목록(장소·아키텍처·연결 상태)과 노드별 능력 이름 — 짧게 |
| `capabilities(filter)` | 조건에 맞는 능력 인스턴스와 사양(범위·분해능·오차·지연·안전 등급) |
| `capability_detail(id)` | 한 능력의 자세한 설명(지도 노드) — 칩 이름은 여기서도 숨김 |
| `propose_plan(plan)` | 허브가 검증: 능력 존재, 정책(allow/confirm/deny), 노드 한계, 사양 충족 여부 → 통과/이유 |

검증이 "사양 미달"(예: 0.1 °C 분해능 요구인데 0.5 °C 센서뿐)이면 허브가 그 이유를 돌려주고 모델이
계획을 고치거나 "할 수 없다"고 말한다. **불가능을 정확히 말하는 근거가 사양**이다.

지금 허브의 모델은 Claude Code CLI(`claude -p`)라 API의 tool_use를 직접 쓰지 않는다. 같은 모양을
두 방법으로 낼 수 있다: (a) 에이전트 모드에서 허브 CLI(`body.py capabilities …`)를 허용 도구로 주기 —
인큐베이팅이 `probe.py`로 이미 쓰는 방식, (b) 한 번의 호출에 요약을 주고 JSON 계획만 받기.

**2단계 — op 작성(노드마다).** 허브가 그 이미지만을 위한 헤더 `poke_body.h`를 **생성**한다.

```c
/* poke_body.h — generated for living-pi4, sentence r-1a2b, 2026-11-02 */
#include "emc1403.h"
typedef int cap_t;
#define CAP_LIVING_TEMP   0          /* 거실 창가 온도 — temperature.read */
static inline int cap_temperature_read(cap_t c, long *mc) {
    switch (c) { case CAP_LIVING_TEMP: return emc1403_read(0x18, 1, mc); }
    return -1;
}
```
모델은 `cap_temperature_read(CAP_LIVING_TEMP, &v)`만 쓴다. manifest도 능력 단위가 된다
(`{"cap": "CAP_LIVING_TEMP"}`), 허브가 그것을 함수 → 레지스터 → 허용 목록으로 기계적으로 펼친다.

이것이 컴파일러 쪽에서 생기는 변화다.
- **묶음은 컴파일이 한다.** 칩이 바뀌거나 주소가 옮겨지면 헤더만 다시 생성하고 op 소스는 그대로다. 같은 능력을 가진 다른 칩으로 바꿔도 카드가 재발음 없이 이어진다.
- **헤더도 휘발한다.** 문장마다, 노드마다 생성되고 이미지와 함께 버려진다. 노드에 HAL이 상주하지 않는다 — HAL이 문장의 몫만큼만 매번 만들어진다.
- **상수가 접힌다.** `switch`가 하나뿐이면 인라인되어 칩 함수 호출 하나로 줄어든다. 노드마다 다른 기계어가 나온다.

## 4. 인큐베이팅의 새 정의

인큐베이팅 = **식별 → 조각 작성 → 기능 검증 → 능력 묶기 → 사양 실측 → 안전 심사 → 등록(L2) → 지도 재생성**

새로 들어가는 두 단계:
- **능력 묶기:** 조각 함수 중 어느 것이 어느 L3 인터페이스의 어떤 채널을 구현하는지. 모델이 제안하고, 허브가 인터페이스 시그니처와 맞는지 컴파일로 확인한다(어댑터를 생성해 빌드).
- **사양 실측:** 손잡이가 있는 부품은 값을 훑어 범위·분해능·오차를 잰다(소수점 온도 결함이 여기서 잡힌다). 손잡이가 없으면 데이터시트 기준으로 표시한다.

## 5. 저장 — 한 곳, 소유자가 분명하게

```
hub/registry/
  nodes/<node>.json           L0   부팅 경로 발견·사람 등록이 쓴다
  attachments/<node>.json     L1   스캔·identify(bodycheck)가 쓴다
  parts/<part>.json           L2   인큐베이팅만 쓴다 (안전 심사를 거친 것만)
  caps/<cap>.json             L3   사람이 쓴다 (어휘는 작게, 버전 관리)
  policy.json                 집주인의 정책 (allow/confirm/deny 덮어쓰기)
```
지금의 `catalog.json`, `fn.json`, `wiring.json`, `bench.py` 목록은 이 기록부에서 **생성**되는 쪽으로 옮긴다
(한동안은 양쪽을 맞추는 변환기로). RouteMind 지도와 모델용 요약도 기록부에서 생성한다.

## 6. 어려운 곳 (정직하게)

1. **어휘 설계.** 너무 작으면 표현이 안 되고, 너무 크면 리눅스의 ioctl 꼴이 된다. 기준: 측정량·행위 단위로 나누고, 장치 고유 기능은 어휘에 넣지 말고 부품 형의 "고급 함수"로 남긴다(모델에게는 기본으로 안 보임).
2. **채널과 상태.** 한 칩이 여러 채널(EMC1403 3채널, PCA9555 16핀)을 가진다. 능력 인스턴스 = 부착 × 채널. 행위 능력은 상태(켜짐/꺼짐)를 가지는데, 노드에는 상태가 남지 않으므로 상태는 장치 자신과 허브의 기록에만 있다.
3. **초기화가 필요한 부품.** 설정 쓰기가 필요한 센서(DPS310 차가운 칩)는 "읽기 능력"인데도 쓰기 권한이 필요하다 — 능력 묶음에 `needs_config` 표시와 별도 정책.
4. **시간이 중요한 능력.** IR 송신처럼 마이크로초 파형은 op가 못 한다 → 런타임 원시 통로(시스템콜 계약서의 기준)로 들어가고, 능력은 그 통로 위의 조각이 구현한다.
5. **노드 간 데이터.** 별 모양(허브 경유)을 유지하면 다음 단계의 입력을 다음 이미지의 상수로 굽는다. 계획의 `after`가 그 순서를 정한다.
6. **ABI 버전.** L3 인터페이스와 `rt.h`는 오래 가야 한다. 노드의 `abi`와 인터페이스의 `v`가 맞지 않으면 계획 단계에서 거절.

## 7. 첫 단계 제안

1. 지금 부품 12개를 위 L2 모양으로 옮기는 변환기 + L3 어휘 v0(9개) 작성.
2. `poke_body.h` 생성기와 능력 단위 manifest → 기존 op 작성 경로 하나(온도 읽기)를 능력 API로 바꿔 regress 통과.
3. 트윈에 노드 두 대(각자 다른 부품)를 두고 `body_summary` / `capabilities` / `propose_plan` 도구로 "두 방 중 더 더운 곳" 계획을 세워 보기.
4. 그다음 능력 사양 실측을 인큐베이팅에 추가.

## 8. 구현 상태 (2026-11-02)

- `sentence/registry/` — caps 9개(L3), parts 11개(L2, `registry_build.py`가 카탈로그·인큐베이팅 기록·소유자 정책 표에서 생성, 정책은 바인딩이 쓰는 함수 중 가장 엄격한 것), nodes 3개(L0: 거실·침실·다용도실 트윈). 저장소의 `hub/`(예전 Node.js 허브)와 섞지 않으려고 `sentence/registry/`에 둠.
- `sentence/registry.py` — instances(부착 × 능력 × 채널, 상수 이름에 칩 이름 없음), body_summary(모델용), validate_plan, header(poke_body.h 생성 + 허용 목록 유도), build_step, boot_step. L1은 아직 노드 파일의 트윈 목록에서 유도(실기기는 스캔이 써야 함). 사람이 채널에 이름을 붙임(`channel_names`).
- `sentence/plan.py` — 관측 계획 → 검증 → 단계별 op(능력 API만) → 노드별 부팅 → 판단 → (행동 계획 → 검증·확인 → 실행) → 답. 쓰기 대상이 모호하면 추측하지 않고 되물음.
- `sentence/spec_measure.py` — 실험대에 소수 포함 값을 넣어 능력 사양 실측 → parts의 `measured`, 모델 요약에 노출.
- 시험: `teams/registry/t_registry.py`(모델 0회) regress --quick 편입.

실측: "두 방 중 더 더운 방의 천장등을 켜줘" → 침실·거실 동시 관측 → 침실 천장등(핀 0) low→high, 나머지 핀 그대로, 약 20 s. "다용도실 불 켜줘" → 부팅 0회, 정확한 불가 이유. 사양 실측이 EMC1403 조각의 소수 버림(최대 0.75 °C)을 잡음 — 학습 때 검증이 정수 온도만 뽑았기 때문.

남은 것: 인큐베이팅이 능력 묶음·사양 실측을 스스로 하게(지금 BINDINGS는 사람이 씀), 채널 이름이 칩마다 달라 같은 op가 다른 칩으로 그대로 옮겨 가지 않음(채널을 역할 이름으로), 계획 단계의 사양 검사(요구 정확도 vs 실측), 기록부를 원본으로 삼고 catalog/fn.json/wiring을 생성 쪽으로 전환, 실제 스캔으로 L1 채우기, EMC1403 조각 재학습(소수 포함 검증).

## 9. 가상 집 — 세 방 실험대와 세계 규칙 (2026-11-02)

**핀 역할(L1).** GPIO 확장 칩에 꽂힌 "것"을 소유자가 역할로 적는다: `switch`(핀 1), `contact`·`motion`·`leak`(입력 핀 1), `cover`(열기·닫기 모터 핀 + 선택: 닫힘 리밋 핀), `climate`(전원 핀 + 목표 온도 비트 핀들). 역할이 없는 핀은 능력으로 드러나지 않는다. 한 "것"에 상수 하나 — `CAP_LIVING_AIRCON`이 climate.set과 climate.read에 함께 쓰인다. 몸체는 `registry.ROLES`의 틀 + 부품별 `PIN_OPS`(pca9554·pca9555)로 생성; 허용 목록은 여전히 몸체가 부르는 함수에서 유도.

**어휘(L3) 16개.** 기존 9 + contact.read, motion.read, leak.read, cover.move, cover.read, climate.set, climate.read.

**세 방(트윈 raspi4b 노드 3대).**
- 거실: TMP105 0x48 실내 온도 · PCA9554 0x20 (P0 천장등, P1 간접등, P2/P3 커튼 열기/닫기, P4 움직임, P5 커튼 닫힘 리밋) · PCA9554 0x21 에어컨 대역 (P0 전원, P1–P5 목표 온도) — IR 송신의 자리.
- 침실: TMP421 0x4c (실내, 창가) · PCA9554 0x20 (P0 천장등, P1 스탠드, P4 창문).
- 다용도실: ADM1272 0x10 세탁기 플러그 전력 · EMC1413 0x18 (칩 자체=공기, 보일러 공급관, 환수관) · PCA9554 0x20 (P0 세탁기 플러그, P4 누수).
- QEMU의 `pca9535`는 이름과 달리 LED 선택 레지스터 모델(led0–15, on/off/pwm)이라 핀 진실이 의미가 없음 → 에어컨 대역은 PCA9554로.

**세계(`sentence/world.py`).** QEMU 부품 사이에는 물리가 없다. 허브 옆 세계가 그것을 맡는다(노드는 모름):
부팅 전 세계 → 손잡이(센서 값, 모든 역할 핀의 레벨 — 출력 핀도 지금 상태로 두어 매 부팅이 새 QEMU여도 상태가 이어짐) · 부팅 후 진실 핀 → 세계(스위치·커튼·에어컨이 바뀐 것) · `advance(분)`로 시간 진행. 규칙: 바깥 26±6 °C(15시 최고), 방은 분당 1 %씩 바깥으로(창문 열면 5 %), 거실 서향 햇볕 16:00–19:30(커튼 닫으면 25 %), 침실 동향 아침 햇볕, 에어컨은 목표까지 분당 최대 0.1 °C, 세탁기 플러그 켜면 45분 400 W, 보일러 06–08·18–22시 55 °C. `POKE_WORLD=<파일>`일 때만 켜짐.

**계획(L4) 확장.** 읽기 단계는 `repeat {every_min, times}`로 시간에 걸쳐 관측(같은 일정의 반복 단계는 매 시각 나란히 부팅 — 시계 하나, 이미지는 단계마다 한 번 빌드). 기다리는 행동은 상시 의도 `standing {watch, every_min, until_min, when {key, op, value, times}, then, say_ko}` — 감시 이미지는 한 번 빌드해 반복 부팅, 조건이 연속 times번 맞으면 행동 한 번. 행동은 반복 불가, 감시는 읽기만. 기다림은 지금 가상 세계에서만(실제 허브 타이머는 아직).

**실측(모델 경유, 세계 파일 각각).**
- "거실 커튼 닫고 에어컨 24도로 켜줘"(17:00) → 상태 관측 → 커튼 닫기 + 에어컨 24 → 세계 반영, 60분 뒤 27.0 → 25.0 °C. 45 s.
- "거실이 왜 저녁마다 더운지 찾아줘"(13:00) → 거실(온도·에어컨·커튼·움직임·조명)·보일러·침실 3단계 × 12회 30분 간격 → "거실만 16:30 이후 빨라짐, 커튼 종일 열림, 에어컨·조명 꺼짐 → 서향 저녁 햇볕 유력(창 방향은 추정)", 보일러 18:30 가동도 따로 지적, 행동 없이 제안만. 69 s.
- "외출하면 에어컨 꺼줘" → 움직임 10분마다, 0이 3회 연속이면 끔 → 세계 각본(17:40 외출)에 18:00 발동, 에어컨 꺼짐. 23 s.
- 첫 시도에서 드러난 것: 반복 단계가 차례로 돌며 시계를 각자 밀었음(침실이 새벽 4시부터 관측) → 나란히 부팅으로; 다채널 요약이 첫 id만 보여 보일러 칩 자체 온도만 읽음 → 모든 id 나열 + 채널 이름; 커튼 위치를 못 읽어 "추정" → 닫힘 리밋 핀과 cover.read.
- 시험: `teams/registry/t_world.py` 8/8(모델 0회) regress --quick 편입, 157/157.
