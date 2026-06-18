# POKE Protocol v1.0

**Protocol for Open Kernel Execution**

POKE is a protocol for AI to directly control hardware. No OS, no drivers, no apps.

---

## Concepts

```
Hub   — LLM brain. Interprets commands, generates code, orchestrates edges.
Edge  — Bare-metal runtime. Receives and executes machine code.
Profile — Device description (registers, peripherals, capabilities).
```

---

## 1. Enroll — Edge Registration

Edge announces itself to the hub.

```
POST /enroll

{
  "node_id": "rpi-kitchen",
  "arch": "aarch64",
  "memory_mb": 512,
  "capabilities": ["compute", "gpio", "audio"],
  "endpoint": "http://192.168.1.42:8080"
}

→ { "ok": true, "enrolled": "rpi-kitchen" }
```

## 2. Discover — List Edges

```
GET /nodes

→ {
  "nodes": [
    {
      "node_id": "rpi-kitchen",
      "arch": "aarch64",
      "status": "alive",
      "last_seen": "2026-05-27T07:00:00Z",
      "capabilities": ["compute", "gpio", "audio"]
    }
  ]
}
```

## 3. Poke — Code Injection

Hub sends machine code to edge for execution.

```
POST {edge}/poke

Body: raw machine code (binary)

→ { "eax": 4 }          (x86)
→ { "x0": 4 }           (ARM64)
→ raw bytes              (image/audio data)
```

## 4. Relay — Natural Language Command

Mobile/voice edge sends natural language to hub. Hub interprets, generates code, routes to appropriate edge.

```
POST /relay

{
  "from": "mobile-abc",
  "command": "calculate 100 * 7",
  "target": "auto"          // optional: specific edge or "auto"
}

→ {
  "plan": {
    "type": "compute",       // compute | draw | reply | broadcast
    "target": "x86-qemu",
    "task": "multiply 100 by 7"
  },
  "result": "eax=700\n"
}
```

### Plan Types

| Type | Description | Target |
|---|---|---|
| `compute` | Math/logic → assembly execution | CPU edge (x86, ARM) |
| `draw` | Visual → pixel generation → IMG protocol | Display edge |
| `reply` | Result goes back to sender | Sender edge |
| `broadcast` | Show on all edges | All |

## 5. Voice — Audio Command

Raw audio sent to hub for STT → relay pipeline.

```
POST /voice?from=arm-device&hint=optional+text

Body: WAV audio (8kHz, 16-bit, mono)

→ {
  "transcript": "100 곱하기 7",
  "plan": { "type": "compute", "target": "x86-qemu", ... },
  "result": "eax=700\n",
  "reply_text": "결과는 700입니다",
  "reply_audio_base64": "UklGR...",
  "reply_audio_size": 27734
}
```

## 6. Health — Heartbeat

```
GET {edge}/health

→ {
  "status": "alive",
  "arch": "i386",
  "memory_mb": 64,
  "gfx": false
}
```

## 7. IMG — Image Protocol

Binary protocol for sending pixel data to edges with displays.

```
Bytes: "IMG" (3) + width (2 LE) + height (2 LE) + RGB pixels (w*h*3)
```

## 8. FRM — Streaming Protocol

Frame-based streaming over raw TCP.

```
Per frame: "FRM" (3) + width (2 LE) + height (2 LE) + RGB pixels (w*h*3)
```

## 9. Serial Protocol (UART)

For edges connected via USB serial (UART). Supports both TCP bridge and direct serial port.

```
Request:  "POKE" (4) + payload_len (4 LE) + payload
Response: "RESP" (4) + payload_len (4 LE) + payload

Payload commands:
  "PING"                      → "PONG"
  "INFO"                      → JSON device info
  "EXEC" + code_bytes         → execution result
  "GPIO"                      → JSON GPIO states
  "TONE" + freq(2) + dur(2)   → play audio tone
```

### Serial Enrollment

```
POST /enroll

{
  "node_id": "esp32-c3",
  "arch": "riscv32",
  "memory_mb": 4,
  "endpoint": "serial:///dev/ttyUSB0"
}
```

Baud rate defaults to 115200. Override with query parameter:
```
"endpoint": "serial:///dev/cu.usbserial-110?baud=9600"
```

### Serial Port Discovery

```
GET /serial/ports

→ [
  { "path": "/dev/ttyUSB0", "manufacturer": "Silicon Labs", "vendorId": "10C4" }
]
```

---

## Device Profile

A profile describes what a device can do — its registers, peripherals, and capabilities. Profiles enable LLM to generate correct machine code for any device.

```json
{
  "profile_id": "stm32f401-generic",
  "chip": "STM32F401",
  "arch": "arm-cortex-m4",
  "clock": "84MHz",
  "memory": { "flash": "512KB", "ram": "96KB" },
  "peripherals": {
    "gpio": { "ports": ["A","B","C"], "pins_per_port": 16 },
    "i2c": { "count": 3 },
    "spi": { "count": 4 },
    "uart": { "count": 6 },
    "adc": { "channels": 16, "resolution": 12 }
  },
  "registers": {
    "GPIO_MODER": "0x40020000",
    "GPIO_ODR": "0x40020014"
  }
}
```

### Profile Accumulation

```
1. Auto-discovery: edge enrolls → hub probes chip ID, scans PCI/peripherals
2. Community: successful commands get saved as profile entries
3. Manufacturer: vendors publish POKE profiles for their devices
```

More profiles = more devices POKE can control = network effect.

---

## Pipeline

```
Human speaks
  → Edge mic captures audio
  → Hub: STT (speech to text)
  → Hub: LLM plans (which edge? what type?)
  → Hub: LLM generates machine code
  → Hub: compiles (nasm)
  → Hub: injects binary to target edge
  → Edge: executes at bare metal
  → Edge: returns result
  → Hub: formats response
  → Hub: TTS (text to speech)
  → Edge speaker plays audio
```

---

## Design Principles

1. **No OS required.** Edge runs bare metal. The protocol IS the OS.
2. **AI-native.** Every command goes through LLM. Natural language is the API.
3. **Profile-driven.** Device profiles are the core asset. More profiles = more power.
4. **Architecture-agnostic.** x86, ARM, RISC-V — if it has registers, POKE can control it.
5. **Hub intelligence, edge simplicity.** Edge only needs: receive bytes, execute, return result.
