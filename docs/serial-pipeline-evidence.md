# POKE Serial Pipeline — Evidence Log

Date: 2026-06-18
Device: ESP32-C3 (QFN32, rev v0.4, 4MB flash)
Connection: USB-Serial/JTAG (`/dev/cu.usbmodem1101`, 115200 baud)
Hub: Node.js, port 3333

---

## 1. Architecture

```
User (natural language)
  │
  ▼
Hub (/relay API)
  │
  ▼
LLM (Claude) ── agentLoop
  │  ↑ tool result
  │  │
  ▼  │
execute_rv tool
  │
  ├─ compileAssemblyRV() ── 내장 JS RISC-V 어셈블러 (RV32IM)
  │
  ▼
pokeNodeSerial()
  │
  ▼  POKE frame: "POKE" + len + "EXEC" + binary
USB Serial (/dev/cu.usbmodem1101)
  │
  ▼
ESP32-C3 (bare-metal)
  ├─ POKE 프레임 파싱
  ├─ IRAM 코드 버퍼에 복사
  ├─ fence.i (instruction cache flush)
  ├─ 코드 실행 (RV32IM)
  └─ RESP frame: "RESP" + len + "a0=결과"
  │
  ▼
Hub ← 결과 수신 → LLM → 사용자 응답
```

---

## 2. Enrollment & Health Probe

### Request
```
POST /enroll
{
  "node_id": "esp32-c3",
  "arch": "riscv32",
  "memory_mb": 4,
  "capabilities": ["compute", "gpio", "temperature_sensor"],
  "endpoint": "serial:///dev/cu.usbmodem1101"
}
```

### Response
```json
{ "ok": true, "enrolled": "esp32-c3" }
```

### Health Probe (`GET /serial/health/esp32-c3`)
```json
{
  "status": "alive",
  "arch": "riscv32",
  "chip": "esp32c3",
  "transport": "usb-serial",
  "free_heap": 310740,
  "uptime": 1186
}
```

### Hub Log
```
[INFO] [enroll] esp32-c3 @ serial:///dev/cu.usbmodem1101 (riscv32, 4MB)
[INFO] [serial] opened /dev/cu.usbmodem1101 @ 115200 baud
```

---

## 3. Arithmetic Pipeline Tests (5/5 PASS)

Hub → RISC-V compile → serial → ESP32 execute → result

| Test | Assembly | Bytes | Result |
|------|----------|-------|--------|
| 3 + 4 = 7 | `li a0,3 / li t0,4 / add a0,a0,t0 / ret` | 16B | **a0=7** |
| 10 * 20 = 200 | `li a0,10 / li t0,20 / mul a0,a0,t0 / ret` | 16B | **a0=200** |
| 100 - 37 = 63 | `li a0,100 / li t0,37 / sub a0,a0,t0 / ret` | 16B | **a0=63** |
| 1 << 8 = 256 | `li a0,1 / li t0,8 / sll a0,a0,t0 / ret` | 16B | **a0=256** |
| fib(10) = 55 | 11 instructions, label+branch loop | 44B | **a0=55** |

---

## 4. LLM Agent Loop — Temperature Sensor Read

### Request
```
POST /relay
{
  "from": "esp32-c3",
  "command": "Read the ESP32-C3 internal temperature sensor..."
}
```

### Agent Execution (Hub Log)
```
[INFO] [agent] from=esp32-c3 -> "Read the ESP32-C3 internal temperature sensor..."
[INFO] [agent] tool: execute_rv({"target":"esp32-c3","asm_code":"li t0, 0x60040000\n..."})
[DEBUG] [agent] execute_rv -> a0=6
[INFO] [agent] done in 3 turns
```

### LLM-Generated Assembly (17 instructions)
```asm
li t0, 0x60040000       # APB_SARADC base address
lw t1, 92(t0)           # Read TSENS_CTRL2_REG (offset 92)
li t2, 0x4000           # bit 14 = XPD_FORCE
or t1, t1, t2           # Set XPD_FORCE
sw t1, 92(t0)           # Write back
lw t1, 88(t0)           # Read TSENS_CTRL_REG (offset 88)
li t2, 0x400000         # bit 22 = PU (power up)
or t1, t1, t2           # Set PU
sw t1, 88(t0)           # Write back
li t3, 500000           # ~3ms delay at 160MHz
loop_delay:
addi t3, t3, -1
bne t3, zero, loop_delay
lw t1, 88(t0)           # Read TSENS_CTRL_REG
srli a0, t1, 14         # Extract bits [21:14]
andi a0, a0, 0xFF       # Mask to 8 bits
ret
```

### Result
```
a0=6  (raw temperature sensor value)
```

---

## 5. Stability Test — 3 Consecutive Reads

### Agent Execution
```
[INFO] [agent] tool: execute_rv → a0=60606
[INFO] [agent] done in 2 turns
```

### Decoded
```
a0 = 60606
  → read1 = 6 (60606 / 10000)
  → read2 = 6 (60606 % 10000 / 100)
  → read3 = 6 (60606 % 100)
```

All 3 reads identical — sensor output is **stable**.

---

## 6. LLM Self-Correction Evidence

Earlier session where LLM autonomously fixed a compilation error:

```
Step 1: execute_rv
  ASM: uses ":delay1" label syntax
  Result: "Error: RISC-V assembly compilation failed"  ← FAIL

Step 2: execute_rv                                      ← LLM retried
  ASM: uses "delay1:" label syntax (fixed)
  Result: a0=60606                                      ← SUCCESS
```

Hub log confirming autonomous retry:
```
[INFO] [agent] done in 3 turns  ← 1 failed + 1 success + 1 memory_save
```

The LLM received the compilation error, identified the label syntax issue, corrected the assembly, and re-executed — all within a single `/relay` API call with no external intervention.

---

## 7. Integration Test Suite (16/16 PASS)

File: `test/serial.test.js`
Report: `test-serial-report.json`

```
POKE Serial Integration Tests
Serial port: /dev/cu.usbmodem1101
Hub port: 3335
────────────────────────────────────────────────────────────
  PASS  serial: PING → PONG
  PASS  serial: INFO returns valid JSON
  PASS  serial: GPIO returns pin states
  PASS  compile+exec: li a0, 42 / ret → a0=42
  PASS  compile+exec: 3 + 4 = 7
  PASS  compile+exec: 10 * 20 = 200
  PASS  compile+exec: 100 - 37 = 63
  PASS  compile+exec: bitwise AND 0xFF & 0xF0 = 240
  PASS  compile+exec: left shift 1 << 8 = 256
  PASS  compile+exec: fibonacci(10) = 55
  PASS  hub: enroll serial edge
  PASS  hub: /nodes shows serial edge alive
  PASS  hub: /serial/health probe returns device info
  PASS  hub: poke-raw 5+5=10 via serial
  PASS  hub: poke-raw 7*8=56 via serial
  PASS  hub: /serial/ports lists USB devices
────────────────────────────────────────────────────────────
16 passed, 0 failed, 16 total
```

---

## 8. Autonomous Event Loop (Edge → Hub)

### Design

```
사용자: "36도 이상이면 알려줘"
  │
  ▼
LLM agentLoop
  │ deploy_serial_monitor(temp, above, 36°C, 5s interval)
  ▼
Hub → POKE + "EMON" + config → ESP32
  │
  ▼
ESP32 (autonomous monitoring)
  │ FreeRTOS task: check temp every 5s
  │ 37.1°C > 36°C threshold → fire!
  ▼
ESP32 → EVNT + JSON → Hub
  │
  ▼
Hub serial.js: parse EVNT frame → onSerialEvent callback
  │
  ▼
agentLoop (automatic, no human)
  │ LLM decides: "threshold exceeded"
  ▼
Response logged
```

### Protocol

```
Edge → Hub (unsolicited):
  "EVNT" (4) + payload_len (4 LE) + JSON payload

Payload examples:
  {"type":"gpio","pin":9,"value":0,"edge":"falling"}
  {"type":"temp","celsius":37.1,"threshold":36.0,"op":"above"}
```

### LLM Agent — Monitor Deployment

Request:
```
POST /relay
{ "command": "ESP32-C3 칩 온도가 36도 이상이면 알려줘." }
```

Agent steps:
```
Step 1: deploy_serial_monitor
  → {"ok":true,"monitor":"temp","op":"above","threshold":36.0,"interval_ms":5000}
```

LLM response:
```
✅ 온도 모니터 배포 완료!
- 감지 조건: 온도가 36°C 이상일 때
- 확인 간격: 5초마다 체크
```

### Event Trigger — Hub Log

```
[INFO] [serial] EVNT from /dev/cu.usbmodem101: {"type":"temp","celsius":37.1,"threshold":36.0,"op":"above"}
[INFO] [serial-event] esp32-c3: temp → agentLoop
[INFO] [serial-event] handled: The ESP32-C3 internal temperature has exceeded the 36°C threshold.
```

### Full Flow Verified

| Step | Actor | Action |
|------|-------|--------|
| 1 | 사용자 | "36도 이상이면 알려줘" |
| 2 | LLM | `deploy_serial_monitor(temp, above, 36°C)` 결정 |
| 3 | Hub | EMON config → ESP32 시리얼 전송 |
| 4 | ESP32 | FreeRTOS 태스크에서 5초마다 온도 체크 |
| 5 | ESP32 | 37.1°C > 36°C → EVNT 프레임 자발 전송 |
| 6 | Hub | EVNT 수신 → agentLoop 자동 실행 |
| 7 | LLM | "임계값 초과 확인" 자율 판단 |

No human intervention between steps 3-7.

---

## 9. GPIO Output + New Commands

### GPOS (GPIO Set)
```
POKE + "GPOS" + pin(1) + val(1)
→ RESP + {"pin":3,"value":1}
```

### EMON (Event Monitor)
```
POKE + "EMON" + type(1) + config(N)
  type=0x01: GPIO — pin(1) + edge(1: 0=falling,1=rising,2=both)
  type=0x02: Temp — op(1) + threshold_x10(2 LE) + interval_ms(2 LE)
→ RESP + {"ok":true,"monitor":"gpio"|"temp",...}
```

### ESTOP (Stop Monitors)
```
POKE + "ESTO"
→ RESP + {"ok":true,"stopped":"all"}
```

### INFO (updated — now includes capabilities)
```json
{
  "status": "alive",
  "arch": "riscv32",
  "chip": "esp32c3",
  "commands": ["PING","INFO","EXEC","GPIO","TEMP","GPOS","EMON","ESTOP"],
  "sensors": ["temp_internal"],
  "events": true,
  "free_heap": 310336,
  "uptime": 1791
}
```

---

## 10. New Agent Tools

| Tool | Description |
|------|-------------|
| `read_sensor` | Calibrated sensor read (temp) |
| `set_gpio` | GPIO output control (LED, relay) |
| `deploy_serial_monitor` | Deploy autonomous event monitor (gpio/temp) |
| `stop_serial_monitor` | Stop all monitors |

---

## 11. Files Changed

| File | Description |
|------|-------------|
| `esp32/main/poke_edge_uart.c` | ESP32-C3 USB-Serial/JTAG POKE firmware |
| `esp32/main/poke_edge.c` | Original WiFi firmware (unchanged) |
| `esp32/main/Kconfig.projbuild` | UART/WiFi transport mode selection |
| `esp32/main/CMakeLists.txt` | Conditional source file by Kconfig |
| `esp32/Dockerfile` | Docker build with memprot disabled |
| `hub/serial.js` | Serial transport module (serialized lock) |
| `hub/transport.js` | Serial routing integration |
| `hub/nodes.js` | Serial edge health check (on-demand) |
| `hub/server.js` | `/serial/ports`, `/serial/health/:id` APIs |
| `hub/agent.js` | `execute_rv` serial endpoint routing |
| `PROTOCOL.md` | Serial protocol documentation |
| `package.json` | `serialport` dependency |
| `test/serial.test.js` | 16-test integration suite |
| `test-serial-report.json` | Test report (timestamped) |
