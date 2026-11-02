<p align="center">
  <h1 align="center">POKE</h1>
  <p align="center"><strong>Protocol for Open Kernel Execution</strong></p>
  <p align="center">by <strong>Orvian</strong> — from Orbit and Via, the path that carries intelligence into machines.</p>
  <p align="center">
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue" alt="license"></a>
    <a href="#quick-start">Quick Start</a> &middot;
    <a href="#five-kinds-of-change">Change</a> &middot;
    <a href="#what-never-changes">Invariants</a> &middot;
    <a href="sentence/TESTS.md">Test map</a> &middot;
    <a href="CONTRIBUTING.md">Contributing</a>
  </p>
</p>

---

**One device, many roles — made on the spot, gone after.**

A POKE node has no operating system and keeps none of our code between requests. A hub turns what a
person says into a boot image made for that one request. The node boots it, does the job, reports,
zeroes the memory it used and resets. The next request can make the same machine something else.

```
line1 — one Raspberry Pi 4 node, the same three parts all day (test jig power line, machine temperatures, reject gate)

08:00  day · inspect     image grants: jig power, item sensor, reject gate
       good item   1.96 A → pass     gate low
       short item  3.77 A → reject   gate high
       empty jig          → empty    gate untouched
20:00  night · observe   image grants: three temperatures, jig power — the reject gate is not in this body
       bearing 58.0 °C, gearbox 47.0 °C, control box 31.0 °C, idle 0 W
       a night image that tries to move the gate does not compile
       after each role the node's memory reads back 0 non-zero bytes of 77,824
next   tomorrow's day image is today's, byte for byte
```

That is [`sentence/teams/role/t_role_shift.py`](sentence/teams/role/t_role_shift.py), run on the twin
(QEMU raspi4b) in the regression suite. Every kind of change POKE promises has a test like it.

---

## Five kinds of change

A device here is not a product with a fixed job. It is a body that takes a role for one boot. POKE
sorts its tests by **what changes**, and each test asks the same five questions: does the role work
before the change, does the new one work after, is nothing left behind (memory, permissions, stale
code), does what should carry over carry over, and is the boundary safe.

| | What changes | Shown by | Test |
|---|---|---|---|
| **T1 Purpose** | The same body is asked something new: a sentence, or a plan over several rooms | "두 방 중 더 더운 방의 천장등을 켜줘" (turn on the ceiling light in the warmer of the two rooms) reads both rooms, then switches one pin in one of them | `sentence`, `effects`, `demo`, `registry/t_registry` |
| **T2 Time · role** | The same body takes a different role by the clock: inspection by day, observation by night, a standing "when I leave, turn the AC off" | the table above | `role/t_role_shift`, `watch`, `schedule` |
| **T3 Body** | Parts move, leave, arrive, or get learned | a sensor moved from 0x48 to 0x49 is found by its register signature, and the sentence runs again on the new address | `hw/t_body_change`, `absent`, `verify/t_r2`, `hw/t_learn` |
| **T4 Machine · path** | The same sentence goes to another architecture or boot path | Pi 4 over TFTP, PCs over PXE / iPXE / U-Boot, ESP32-C3 through its ROM loader | `netboot`, `x86/selftest`, `uboot.py selftest`, `esp32.py selftest` |
| **T5 World** | The room around the node changes | the AC's pin goes high → the virtual room cools 27.0 → 25.0 °C in an hour | `registry/t_world` |

```sh
cd sentence && ../sim/body/.venv/bin/python regress.py --list --cats     # the categories and their cases
../sim/body/.venv/bin/python regress.py --quick --cat T2                  # only time / role changes
```

The full map, including what is not built yet (marked XFAIL until it is), is in [`sentence/TESTS.md`](sentence/TESTS.md).

## What never changes

Whatever role the node takes, these hold. They are tested on every run (`regress --quick`: 159 cases,
about 1–2.5 min, no model calls).

| | Invariant | How it is enforced |
|---|---|---|
| I1 | An image cannot hurt the node or the room | The op runs at EL0 behind a page table, with no hardware window. I2C goes only through a supervisor call, checked against an allow list baked into the image (addresses, registers, value ranges, write counts). There is a 20 s timer budget, and an image that fails the static check never boots. Over the network, only images the hub signed boot: the hub uses the Pi 4 bootloader's secure-boot format (`boot.img` + `boot.sig`, RSA-2048), and anything unsigned or altered is refused. |
| I2 | Nothing of ours stays | Before resetting, the runtime zeroes everything the run used. The hub then reads the twin's RAM back and counts the non-zero bytes. Measured: **0 of ~76–80 KB**. |
| I3 | The report can be trusted | Every bus transfer is reported by the runtime on its own `POKE-EFFECTS` line, which the op cannot forge. |
| I4 | The hub holds under load | Concurrent requests, leaked QEMU processes and files, and races on the record store are all tested. |
| I5 | People stay in charge | Writes that change the room ask first. Out-of-range or forbidden requests are refused before anything boots. An ambiguous request is asked back, never guessed. |
| I6 | Drivers tell the truth | Each driver fragment is checked against the twin's ground truth, and its spec is measured on the twin rather than taken from its comments. |

## How it works

```
"거실 커튼 닫고 에어컨 24도로 켜줘"   (close the living-room curtain and set the AC to 24 °C)

hub   the model sees capabilities only — places, things, capability ids; never chips or addresses
      plan (JSON) → the hub checks it against the body registry and the owner's policy → asks to confirm the writes
      per step: the model writes op_main() against cap_*(); the hub generates poke_body.h holding ONLY the
      capabilities that step was granted, adds the runtime and the driver fragments it needs, derives the
      allow list, checks the image
node  power on → boot → op at EL0 → report → zero memory → reset (≈ 20–30 ms awake for a read)
hub   result + the twin's real pin and sensor state → the answer → the sentence card is remembered
```

- **Permission is the header.** A capability that was not granted does not exist in that image's
  `poke_body.h`, so an op that uses it does not compile.
- **The model never sees chips.** The body registry has five layers: node, attachment, part type,
  capability, plan. The model only works from the capability layer up
  ([`docs/design/hub-registry.md`](docs/design/hub-registry.md)).
- **Drivers live on the hub.** They are kept as one header per part. Only the fragments a request uses
  go into its image, and none of them stays on the node.
- **Remembered sentences are fast.** A sentence card holds the op, its parameters, the body it was
  pronounced for, and its result trace. A remembered sentence goes from speech to node reset in
  0.17–0.5 s. When the body changes, the card is pronounced again for the new body.

### The virtual home

Three rooms of twin nodes, plus a small physics simulator beside the hub
([`sentence/world.py`](sentence/world.py)). The simulator moves the sun across the windows, leaks heat
to the outdoors, lets the AC pull a room toward its target, and runs the washer's program. QEMU's parts
have no physics between them; the world gives them some. Before each boot it sets the sensors and pins,
and after each boot it reads back what the actuators did.

```
"거실이 왜 저녁마다 더운지 찾아줘"   (why does the living room get hot every evening?)
  3 nodes × 12 readings, 30 min apart, one image per step
  → "Only the living room keeps rising, and faster after 16:30. The curtain was open all day, the AC and
     lights were off. Likely west sun through the window — the window's direction is an inference."

"외출하면 에어컨 꺼줘"   (when I leave, turn the AC off)
  standing intent: read motion every 10 min, act on 3 empty readings in a row
  → nobody from 17:40 → AC off at 18:00
```

## Quick start

Everything below runs on the twin, with no hardware needed. You need QEMU, the `aarch64-elf` gcc toolchain
(`brew install qemu aarch64-elf-gcc` on macOS), Python 3 (standard library only), and
[Claude Code](https://claude.com/claude-code) logged in. The hub calls `claude -p` and needs no API key.

```sh
git clone https://github.com/CSP911/poke.git && cd poke/sentence
P=python3

$P say.py "온도 알려줘"                                # one sentence, one boot
$P say.py --yes "LED 3번 켜줘"                         # a write (asks first without --yes)
$P ui/server.py                                        # demo page at http://127.0.0.1:8765

POKE_WORLD=/tmp/home.json $P world.py reset 13:00      # the virtual home
POKE_WORLD=/tmp/home.json $P plan.py "거실이 왜 저녁마다 더운지 찾아줘"

$P regress.py --quick                                  # the whole suite, no model calls
```

More commands, numbers and limits are in [`sentence/README.md`](sentence/README.md).

## Status

Working on the twin:
- sentence boot with v1 isolation and measured wipe
- sentence cards
- body change detection and unattended incubation of new parts
- the hub body registry with capability-only planning
- the three-room virtual home and the factory line
- boot paths for Pi 4 (TFTP), PCs (PXE, resident iPXE at 0.04–0.06 s per sentence, U-Boot) and ESP32-C3 (ROM `load_ram`)

Not yet:
- a real Pi 4 netbooted end to end (the first hardware number to measure)
- **role schedules**, i.e. the hub picking a node's role by the clock (T2 R5–R7, written as XFAIL)
- signed boot and tickets on a real Pi: both run on the twin only so far, and the Pi's RNG200 and the UART answer path are untried
- a measured comparison against a Linux baseline
- ESP32-C3 isolation (RISC-V PMP)

## Project structure

```
poke/
├── sentence/                 the hub: one sentence, one boot
│   ├── say.py hub.py         sentence → op → image → check → boot → answer
│   ├── registry.py plan.py   body registry (L0–L4) and multi-node plans
│   ├── registry/             capabilities, part types, nodes; sites/factory
│   ├── world.py              the virtual home's physics
│   ├── regress.py TESTS.md   the suite and its map (I1–I6, T1–T5)
│   └── netboot/ x86.py uboot.py esp32.py   boot paths
├── edge/sentence/            node runtime (aarch64) + driver fragments (hub-only)
├── edge/sentence-x86/        PC runtime (Multiboot / PXE program)
├── edge/sentence-esp32c3/    ESP32-C3 runtime (ROM loader)
├── sim/body/                 incubation: the model learns a new part on the twin
├── docs/design/              hub registry, why POKE must not become Linux
├── docs/vision/              meeting notes and direction
└── hub/ orchestration/ bin/ edge/kernel/   the earlier Node.js hub (below)
```

## The earlier Node.js hub

POKE started as a Node.js hub that injected LLM-written machine code into small bare-metal kernels:
- x86, ARM64 and RV32 in QEMU
- Pi Zero W, Pi 4 with ethernet and display
- ESP32-C3

It used built-in assemblers for four ISAs, and parameterized binaries for distributed compute
(`poke burst`: a prime count over 4 edges, ×3.6). The code is still in `hub/`, `orchestration/`,
`bin/poke` and `edge/kernel/`, and is documented in [PROTOCOL.md](PROTOCOL.md). Its kernels stay
resident on the device. The sentence boot above is where POKE goes from here: nothing of ours stays
on the node.

## 한국어 요약

**장치의 역할은 고정되어 있지 않습니다.** 사람이 말할 때 그 목적에 맞는 몸이 그 자리에서 만들어지고, 일이
끝나면 사라집니다. 같은 노드가 낮에는 불량품을 검사하고 밤에는 설비 변화를 관측합니다. 모든 변환은 시험으로
보장합니다.
- 변환: 목적, 시간·역할, 몸, 기체, 세계
- 불변식: 격리, 휘발(메모리 0 실측), 보고, 허브 견고성, 사람 확인, 부품 진실

자세한 내용은 [`sentence/README.md`](sentence/README.md)(한국어)와 [`sentence/TESTS.md`](sentence/TESTS.md)에 있습니다.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The most useful contributions:
1. **A transformation test.** Pick a change from T1–T5, ask the five questions, and write it model-free.
2. **A part.** Incubate a new I2C part on the twin and measure its spec.
3. **Real hardware.** Netboot a Pi 4 and send the timing.

## Contact

Business inquiries, collaboration, or just curious: **qct8377@gmail.com**
LinkedIn → [linkedin.com/in/cspark911](https://www.linkedin.com/in/cspark911/)
Bug reports and questions → [GitHub Issues](https://github.com/CSP911/poke/issues)

## License

[Apache 2.0](LICENSE)

---

<p align="center">
  <em>"The future doesn't need an operating system."</em>
</p>
