# 문장 하나, 부팅 하나

노드에는 운영체제가 없습니다. 요청과 요청 사이에 노드에서 돌고 있는 우리 코드도 없습니다.
사람이 문장을 말하면 허브가 그 문장 하나만을 위한 부팅 이미지를 조립합니다. 노드는 그 이미지로
부팅해 일을 하고, 결과를 보고하고, 이 부팅이 쓴 메모리를 0으로 지운 뒤 리셋합니다. 리셋 뒤에는
제조사 부트로더만 다음 이미지를 기다립니다.

처음 써 보는 순서와 써 볼 문장은 `teams/product/MORNING.md`에 있습니다. 이 문서는 구조, 명령,
숫자, 한계를 다룹니다.

```
"온도가 25도 넘으면 LED 0번을 켜고, 아니면 꺼줘"
  허브   기록 찾기 ── 같은 문장 / 숫자만 다른 문장 / 같은 뜻 다른 말 ── 없으면 Claude Code가 op 작성
         (또는 되물음 · 할 수 없음 · 예약 실행으로 답함 — 이때는 노드를 켜지 않음)
         manifest 정책 → 장치를 바꾸는 문장이면 확인 → 이미지 조립(런타임 + 드라이버 조각 + op) → 이미지 검사
  노드   전원 → 부팅 → op (EL0, 허용된 I2C 전송만) → 결과·버스 기록 보고 → 메모리 0 채우기 → 리셋
  허브   결과를 한국어로, 실험대의 실제 상태, 기록에 결과 흔적 저장
```

## 구조

| 층 | 파일 | 하는 일 |
|---|---|---|
| 런타임 (노드) | `edge/sentence/` boot.S, rt.c, rt_power.c, pt.h, ustub.S, image.ld | 진입, MMU, op를 EL0에서 실행, svc로만 UART·I2C·시간, 20초 예산, 결함 보고, 리셋 전 0 채우기. I2C는 이미지에 박힌 허용 목록(I2CA) 안에서만 열림. 모든 전송을 `POKE-EFFECTS` 줄로 보고(op가 위조할 수 없음) |
| 드라이버 조각 | `edge/sentence/frags/*.h`, `catalog.json`, `incubated/` | 장치 드라이버를 헤더 하나씩으로 둔 것. 허브에만 있고, 문장이 쓰는 조각만 그 이미지에 들어감 |
| 허브 흐름 | `hub.py` | 한 문장의 단계를 이벤트로 내보냄. CLI와 데모 화면이 같은 흐름을 씀 |
| 기록 | `record.py` → `build/records/` | 문장마다 op, manifest, 숫자 파라미터, 출처(모델·날짜), 들은 표현들, 승인, 최근 결과 20개 |
| 몸 지문 | `body.py` | 지금 몸(실험대 장치·주소, 배선 wiring.json, 조각 카탈로그). 기억된 op가 이 몸에 맞는지 판단하고, 안 맞으면 다시 발음 |
| 같은 뜻 판정 | `intent.py` | 다른 말로 한 같은 요청을 기존 기록으로 처리. 규칙으로 먼저 보고, 필요하면 짧은 모델 판정 |
| manifest·정책 | `manifest.py`, `policy.json`(선택) | op가 쓰는 장치 함수 선언 → I2C 허용 목록 · 확인 여부(allow / confirm / deny) |
| op 작성 | `say.py` `make_op`, `OP_SYSTEM` | Claude Code CLI(`claude -p`, API 키 없음). 컴파일 오류, manifest 오류, 검사 거부가 나면 이유를 붙여 3라운드까지 다시 씀 |
| 이미지 검사 | `say.py` `build`/`check` | 펜스 앞의 v1 린트: 시스템·간접 분기 명령어, 런타임 밖 호출, op 창 밖 상수 주소 접근(V5 이후 창 0개), MANIFEST가 선언하지 않은 조각을 거부하고, 주변장치처럼 보이는 상수는 경고. 이미지마다 펜스(페이지 표)를 대상 밖에서 따라가 확인(pt_test). 거부된 이미지는 부팅하지 않음(캐시된 op도 같음) |
| 지켜보기 (상시 의도) | `watch.py` → `build/watches/` | "30도 넘으면 알려줘": 읽기 전용 점검 op(say.watch_check), 그 읽기만의 허용 목록과 5초 예산으로 고정한 이미지, 60초 이상 간격의 허브 예약 부팅. 판정 say.watch_verdict(쓰기·거부 전송이면 즉시 멈춤, 2회 연속 실패면 멈춤), 몸 변화·메모리 잔여도 멈춤. 조건이 맞으면 알림만 하고, 행동은 사람이 따로 확인(‘항상’ 승인도 적용 안 함). 상한 say.WATCH: 하루 1,440회/감시, 합계 2,880회, 동시 4개, 24시간 만료. 데모 화면이 떠 있거나 `say.py watch run`일 때 돎 |
| 흔적 대조 | `trace_check.py` | 다시 발음한 op의 첫 실행을 카드 흔적과 대조합니다. 결과 키 이름은 판정에 쓰지 않습니다(재생성마다 바뀜, 시험 T9). 쓰기 함수 집합(EFFECTS)이 다르면 불일치로 보고 이전 발음을 유지합니다. 읽은 부품이나 보고하던 값(이름이 아니라 값으로 찾음)이 다르면 '다르게 발음됨'으로 표시합니다. 판정은 카드의 계보에 남습니다 |
| 데모·배우기 | `ui/` 데모 버튼, `say.py learn` | 5분 데모(기록 사본, 읽기·LED만), "배워줘"(실기기 learn.py 작업, 진행 표시) |
| 예약 실행 | `schedule.py` | 20초를 넘는 요청은 허브가 나눠서 여러 번 부팅. 기다리는 동안 노드는 꺼져 있음 |
| 메시지 | `messages_ko.py` | 한국어, traceback 없음, 결과 라벨·단위·조사 |
| 실험대 (트윈) | `bench.py`, `say.boot` | QEMU raspi4b + I2C 1번 버스의 가상 장치. 기본: TMP105 0x48, DS1338 0x68, LSM303 0x1e, PCA9552 0x60. `POKE_BENCH=ext`면 TMP421 0x4c, PCA9554 0x20, SSD0303 0x3c 추가. 기록부 노드(세 방 집, 공장 line1)는 노드 파일의 트윈 목록으로 부팅 |
| 새 장치 | `incubate.py`, `probe.py` | 버스 스캔 → 식별 → 조각 작성 → 트윈에서 검증 → 등록(실기기팀) |
| 실기기 | `netboot/`, `rtcfg.py`, `say.py --pi` | Pi 4 EEPROM 부트로더가 TFTP로 이미지를 가져감. 이미지 안의 RCFG 블록이 대상별 설정(워치독, I2C 분주) |
| 서명 부팅 | `seal.py`, `netboot/vboot.py --signed` | 허브가 최종 이미지를 Pi 서명 부팅 형식(boot.img + boot.sig, RSA-2048)으로 봉인. 가상 부트로더가 확인하고 서명 없거나 바뀐 이미지는 켜지 않음. 켜진 뒤에는 런타임이 일회용 값으로 허브의 티켓을 받아야 op를 돌림 — 다시 보낸 옛 이미지는 아무것도 하지 않음. 키는 `keys/`(커밋 안 됨) |
| 데모 화면 | `ui/server.py`, `ui/index.html` | 표준 라이브러리 HTTP + SSE 한 페이지 |
| 회귀 시험 | `regress.py`, `TESTS.md` | 모든 팀이 단계마다 돌리는 시험. 케이스마다 범주: 불변(I1 격리 … I6 부품 진실) / 변환(T1 목적, T2 시간·역할, T3 몸, T4 기체, T5 세계) |
| 몸 기록부 | `registry.py`, `registry/`, `registry_build.py` | L0 노드 / L1 부착(핀 역할: switch·contact·motion·leak·cover·climate) / L2 부품 형 / L3 능력 어휘 16개 / L4 계획. 모델은 능력만 보고, 이미지마다 허가된 능력만 담은 `poke_body.h` 생성. `POKE_SITE=factory`면 공장 현장(line1) |
| 계획 | `plan.py` | 목적 → 관측 계획 → 검증 → 노드별 이미지·부팅 → 판단 → 행동(확인) → 답. 반복 관측(repeat), 상시 의도(standing) |
| 가상 세계 | `world.py` | 허브 옆 물리(햇볕, 열 손실, 에어컨, 세탁기, 보일러). 부팅 전 센서·핀 설정, 부팅 후 행동 반영. `POKE_WORLD=<파일>`일 때만 |

## 명령

```sh
cd sentence
P=../sim/body/.venv/bin/python

$P say.py "온도 알려줘"                      # 문장 하나
$P say.py --yes "LED 3번 켜줘"               # 확인 질문에 자동으로 예 (--no: 아니오)
$P say.py "불 좀 켜줘" --answer "2번"         # 질문(확인·되물음)에 미리 답함
$P say.py --show --debug "온도 알려줘"        # op 소스, 영어 서술, 원시 결과, 내부 오류
$P say.py "온도가 30도 넘으면 알려줘"         # 지켜보기 시작(확인 받음) — watch / watch run / watch stop all / watch events
$P say.py memory                             # 허브가 기억하는 문장
$P say.py memory "온도 알려줘"                # 한 문장의 카드(들은 말, 장치, 승인, 출처, 계보, 결과 흔적)
$P say.py forget "온도 알려줘"                # 기록 지우기(확인을 받음)
$P say.py unapprove "LED 3번 켜줘"            # '항상' 승인 취소
$P say.py card export "온도 알려줘" card.json  # 카드 내보내기 / card import card.json [--replace]
$P say.py --body teams/product/bodies/tmp105-0x4a.json "온도 알려줘"   # 다른 몸(TMP105를 0x4a로)
$P say.py selftest                           # 조각마다 실험대 실제 값과 대조

$P ui/server.py                              # 데모 화면 http://127.0.0.1:8765  (#문장을 붙이면 바로 실행)
POKE_BENCH=ext $P ui/server.py               # 새 장치가 붙은 실험대
POKE_BODY=teams/product/bodies/tmp105-0x4a.json $P ui/server.py      # 다른 몸으로
POKE_RECORDS=/tmp/hub2 $P ui/server.py       # 기록이 빈 두 번째 허브(카드 옮기기 시연)

$P regress.py --quick                        # 모델 호출 없이 약 70초
$P regress.py --quick --cat T2               # 한 범주만 (시간·역할 변환) — 범주는 TESTS.md
$P regress.py --full                         # 새 문장 생성, 넷부팅, 예약 실행 포함

$P say.py --preflight                        # 실기기 준비 점검(보고선, 10.0.0.1, TFTP, 펌웨어, config)
$P say.py --pi --preflight "온도 알려줘"      # 점검이 통과할 때만 실기기로 보냄
$P say.py --netboot-twin "LED 3번 켜줘"       # 넷부팅 경로를 가상 부트로더로 (netboot/README.md; vboot.py --signed면 서명 확인)
```

종료 코드: 0 정상, 1 결과에 문제, 3 허브가 멈춤(확인 없음, 되물음, 범위 밖, 정책, 할 수 없음, 실기기 준비 안 됨), 4 오류.

### 확인과 정책

`manifest.py`의 기본값과 `policy.json`이 정합니다.

- 읽기(온도, 시계, 자기 센서, LED 입력)는 바로 실행합니다.
- `led_set`은 확인을 받습니다. `y`는 이번만, `a`는 항상이며, 항상을 고르면 기록에 저장됩니다.
- `rtc_set`(시계 쓰기)은 거부합니다.
- 목록에 없는 함수도 거부합니다.

센서 설정 쓰기(TMP105 12비트, 자기 센서 측정 모드)는 읽기의 일부로 봅니다. 노드가 op를 멈추면 이유를
붙여 새 op를 한 번 받습니다. 단, 장치를 바꾸는 쓰기가 이미 버스에 닿았다면 다시 묻습니다.

### 기록과 재사용

다음 순서로 찾고, 앞에서 찾으면 뒤는 하지 않습니다.

1. 같은 문장. 띄어쓰기, 문장부호, "좀·한번", 조사 을/를/이/가/은/는, 끝의 "요"는 무시합니다.
2. 숫자만 다른 문장. "LED 7번", "30도", "0.5초"는 `#define P_x`만 바꿔 다시 빌드합니다(0.1초).
3. 같은 뜻 다른 말(`intent.py`).
   - 규칙: 한 장치를 그냥 읽는 문장이면 모델을 부르지 않습니다. "지금 몇 도야?", "현재 온도는?",
     "현재 시각 알려줘"가 여기에 해당합니다.
   - 짧은 판정: 규칙으로 정하지 못하고 후보 기록이 있을 때만 짧은 모델 판정 1회(4–5초)를 씁니다.
   - 가드: 장치 집합, 구조 표지(조건·시간·켜기/끄기/깜빡임·변환·'만'·부정), 숫자 개수가 같아야
     합니다. 이 가드 때문에 "온도가 30도 넘으면 LED 켜줘"는 "온도 알려줘"로 처리될 수 없습니다.
   - 시험 문장 모음: `teams/product/paraphrase-corpus.json`
4. 옛 `build/ops` 캐시. 기록이 없을 때만 씁니다.
5. 새 op 작성.

범위 밖 숫자("LED 20번", "LED -1번", "LED 3.5번", TMP105가 못 재는 온도 기준)와 정책상 금지("시계를 오후 3시로 바꿔줘")는 op를 쓰기 전에 허브가 거릅니다(intent.prefilter, 모델 0·0.04초). 최종 강제는 여전히 런타임입니다. 같은 새 문장이 동시에 오면 say.key_lock으로 한 번만 씁니다.

### 카드와 다른 몸 (더늠)

기록 하나가 문장 카드입니다. 카드는 대표 문장, 들은 말, 의도(manifest), 숫자 범위, 결과 흔적, 출처,
계보로 이루어지고, op는 그 문장을 어떤 몸에서 한 번 발음한 것일 뿐입니다.

- **장치 주소:** op는 주소를 `POKE_ADDR_<조각>` 상수로만 씁니다. 허브가 빌드 직전에 배선(wiring.json)을
  보고 값을 채우므로, 부품을 다른 주소로 옮겨도 다시 빌드만 하면 됩니다(0.1초).
- **몸에 안 맞을 때:** 주소를 숫자로 박은 op나 필요한 조각이 없는 몸이면, 허브가 몸 지문을 비교합니다.
  계보에 이 몸용 발음이 있으면 그것을 씁니다(모델 0). 없으면 문장을 이 몸에 맞게 다시 발음하고,
  이전 op는 계보로 보냅니다.
- **결과 흔적:** 흔적마다 어느 몸에서 났는지를 남깁니다.
- **카드 옮기기:** 카드를 내보낼 때 승인은 넘기지 않습니다. 승인은 그 몸의 주인 것입니다.

시연한 것: 기본 실험대에서 만든 「온도 알려줘」 카드를 기록이 빈 두 번째 허브로 가져가
TMP105를 0x4a로 옮긴 몸에서 실행했습니다.
1. 허브가 "0x48에서 찾지만 이 몸에서는 0x4a"를 감지하고 문장을 다시 발음했습니다(5.9초). 결과는 정상이었습니다.
2. 같은 몸에서 "지금 몇 도야?"도 같은 카드로 처리됐습니다(규칙, 모델 0).
3. 기본 몸으로 돌아와도 같은 op가 그대로 맞았습니다(주소 상수).

## 숫자 (트윈, 2026-10-11)

- 기억된 문장(숫자만 바꾼 것 포함): 말부터 노드 리셋까지 0.17–0.5초. 노드가 켜져 있는 시간은 읽기 20–30 ms,
  TMP105 첫 12비트 변환이 있으면 약 330 ms.
- 새 문장: op 작성 6–21초, 거의 모두 1라운드에 통과.
- 같은 뜻 판정: 규칙은 즉시, 모델 판정은 4–5초.
- 되물음, 할 수 없음, 범위 밖, 정책 거부: 부팅 0회.
- 이미지 약 16.5–20.5 KB(v1 격리 런타임과 효과 기록 포함), op 0.2–1.4 KB.
- 비움 실측: 노드가 리셋된 뒤 허브가 트윈 메모리를 읽어 보니, 이 부팅이 쓴 영역(op·런타임 데이터·스택) 약 76–80 KB 중 0이 아닌 바이트 0. CLI와 화면에 그 값이 그대로 나옵니다. 넷부팅 트윈과 Pi는 실측이 없어 이미지에서 읽은 범위만 표시합니다.
- 예약: "30초 동안 10초마다 온도"는 10초 간격 부팅 4회입니다.
- 회귀: `--quick` 159건 약 70–150초(모델 호출 0), `--full` 약 2분. 범주(불변 I1–I6, 변환 T1–T5)는 `TESTS.md`.

## 한계

- **실기기 미검증.** 실제 Pi 4는 아직 넷부팅해 보지 않았습니다. 화면과 숫자는 모두 QEMU 트윈 기준입니다.
  첫 하드웨어 숫자는 넷부팅 시간이며, 추정치는 1.5–7초입니다(`netboot/README.md`).
- **트윈 실험대는 매 부팅 새로 시작합니다.** 데모 화면은 LED 상태를 다음 부팅에 넘겨 실제 칩처럼
  보이게 하지만, CLI와 시험은 매번 새 실험대에서 돌아갑니다.
- **이미지 검사는 정적 검사입니다.** 모든 경로에 대한 증명은 아닙니다. 끝나지 않는 루프는 GIC 타이머의
  20초 예산이 끊습니다(트윈에서 확인). 실기기 워치독은 RCFG로 켜지지만 아직 검증하지 않았습니다.
  버스에 대한 실제 보장은 런타임의 허용 목록입니다.
- **같은 뜻 판정의 어휘는 작습니다.** 규칙이 모르는 낱말이 있으면 모델 판정이나 새 op로 넘어갑니다.
  잘못된 매칭은 가드로 막고, 그래도 생기면 기록 파일에서 그 표현을 지우면 됩니다.
- **결과 문구는 op가 붙인 라벨을 그대로 씁니다.** 노드 서술은 한국어지만, 이전에 만든 op는 영어로
  서술하며 이 영어 문장은 `--debug`에서만 보입니다.
- **기록은 `build/` 아래의 사용자 데이터입니다.** 커밋 대상이 아닙니다. 조각 카탈로그가 바뀌어도 기록은
  유지하며, 빌드나 검사에 실패하면 다시 씁니다.

## x86 PCs over PXE (`x86.py`, `edge/sentence-x86/`)

The same sentence boot on an ordinary PC: the PC's network boot (iPXE) fetches a Multiboot image over
TFTP, the op computes in ring 3 behind a page table (no I/O ports, no runtime memory), the runtime
reports on COM1, zeroes everything the run used and resets the PC — whose next boot, with no sentence
pending at the hub, falls through to its own disk. Same op API (`rt.h`); no devices — compute jobs.

```sh
../sim/body/.venv/bin/python x86.py "1부터 100만까지 숫자 중 각 자리 숫자의 합이 7의 배수인 것은 몇 개야?"
../sim/body/.venv/bin/python x86.py --netboot "…"      # through the hub's tftpd (answers only while pending)
../sim/body/.venv/bin/python x86.py --parts 4 "1부터 2천만까지 소수가 몇 개인지 세줘"   # 4 PCs at once, sum_* fields added
../sim/body/.venv/bin/python x86.py selftest           # fences: port, runtime memory, null, stack, bad pointer, big result
```

Twin (QEMU `-M pc`, e1000 iPXE): selftest 7/7, memory read back after every reset has 0 non-zero bytes;
PXE boot ≈ 6.4 s (iPXE DHCP + TFTP), a split prime count over 4 PCs 6.4 s end to end, correct
(1,270,607 below 2×10⁷). With nothing pending, the hub's tftpd ignores the PC and none of our code runs.
Not done yet: real PCs (BIOS network-boot order, Wake-on-LAN, Secure Boot, BitLocker), UEFI PXE
(the twin uses legacy BIOS PXE), and time budgets beyond 20 s per boot.

**Where the 6.4 s went, and the resident mode.** Packet timestamps: ≈3.2 s BIOS + NIC ROM + iPXE
banner/link, 3.05 s iPXE waiting for proxy-DHCP offers after the first offer (QEMU's DHCP sends no
PXEClient option), 0.1 s DHCP-ack → TFTP → our code; loading the same image directly takes 0.04 s.
So `x86.py --station "…" "…"` boots the PC once into iPXE (`loop.ipxe` chains the hub's tftpd, which
answers only while a sentence is pending); each sentence is then a returning PXE program
(`edge/sentence-x86/nbp.S` + the same payload): it runs, zeroes the payload at 1 MiB and its copy
after the stub, restores iPXE's interrupt table, PIC and timer, and returns. Measured: sentence pending →
our code 0.04–0.06 s, back in iPXE 0.04–0.06 s after a short op, both copies read back all zero; a
faulting op and a timed-out op return the same way and the next sentence runs. What stays of ours
between sentences: the ~0.4 KB stub at 0x7C00 (no part of the op) until iPXE's next load overwrites it.

## Edges without PXE: U-Boot and a chip's ROM loader

**U-Boot TFTP (`uboot.py`).** For boards whose boot loader is U-Boot. Twin: QEMU's PC with U-Boot as its
firmware (`build-ext/u-boot-poke-x86.rom`); its boot command asks the hub's tftpd for `sentence-x86.elf` in a
loop and `bootelf`s it — the same x86 image as the PXE path; the image resets the PC at the end and U-Boot asks
again. `uboot.py selftest` 3/3: U-Boot asking 2.4 s after power-on, pending → our code 0.8–1.0 s (U-Boot's
TFTP, ~18 KB/s here), back in U-Boot asking 2.4 s later. Notes: current U-Boot's `bootelf` only jumps when
`autostart=yes` (set around it), `autoload=no` stops `dhcp` fetching a kernel.

**ESP32-C3 ROM download mode (`esp32.py`, `edge/sentence-esp32c3/`).** No flash written, nothing of ours on the
chip: the strapping pins hold it in the ROM's serial download mode; the hub writes the image into SRAM with
the ROM loader (esptool `load_ram`) and the ROM jumps to it; the runtime (same `rt.h`) reports on UART0 through
the ROM's UART routine, zeroes the RAM it used, and resets the chip in software — still strapped, it comes
back in the ROM waiting. Twin: Espressif's QEMU (`esp32c3`, strap 0x02). `../build-ext/espvenv/bin/python esp32.py
selftest` 3/3: images ≈3 KB, ROM sync 0.01 s, load 0.4 s, run → reset 0.05 s; after a reset the 74 KB the run
used read back all zero (the op's text gone); what stays is the 32-byte scrub routine. v0: no isolation yet
(the op runs in machine mode; a RISC-V PMP fence is the next step); delays use the 16 MHz system timer (the ROM's
cycle-counted delay runs fast under emulation). Needs, on hardware: USB-serial + control of BOOT (GPIO9) and EN.
