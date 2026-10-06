"""AI-driven incubation: a language model is given a POKE edge's I2C bus
service — scan, read, write — and nothing else, and has to work out what is
attached. On the twin the devices are QEMU models whose state we control, so
its conclusions can be checked against ground truth.

    .venv/bin/python incubate.py                 # twin with 4 undisclosed virtual devices
    POKE_HOST=10.0.0.2 .venv/bin/python incubate.py   # the real Pi's GPIO 2/3 bus

Output: the model's probing transcript, its conclusions, a draft library entry
per device (build/incubated/0x??.json), and the ground-truth check.
"""
import os, re, sys, json, time, struct
import pokeedge, routemind

ROOT = pokeedge.ROOT
HOST = os.environ.get("POKE_HOST", "twin")
for line in open(os.path.join(ROOT, ".env")):
    m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
    if m and m.group(1) not in os.environ:
        os.environ[m.group(1)] = m.group(2)
import anthropic

MODEL = os.environ.get("POKE_MODEL", "claude-sonnet-5")
TRUTH_TEMP_MDEG = 27500
VIRTUAL = ["-device", "tmp105,id=t1,bus=i2c-bus.1,address=0x49",
           "-device", "lsm303dlhc_mag,bus=i2c-bus.1,address=0x1e",
           "-device", "ds1338,bus=i2c-bus.1,address=0x68",
           "-device", "pca9552,bus=i2c-bus.1,address=0x60"]

TOOLS = [
    {"name": "i2c_scan", "description": "Scan the bus: returns the 7-bit addresses that acknowledge.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "i2c_read", "description": "Write one register pointer byte, then read n bytes from the device. "
     "reg=null reads n bytes without writing a pointer first.",
     "input_schema": {"type": "object", "properties": {"addr": {"type": "integer"}, "reg": {"type": ["integer", "null"]},
                                                       "n": {"type": "integer", "minimum": 1, "maximum": 32}}, "required": ["addr", "n"]}},
    {"name": "i2c_write", "description": "Write bytes to the device (first byte is usually the register pointer). "
     "Use sparingly and only when a read cannot answer the question; never write to registers you have not read.",
     "input_schema": {"type": "object", "properties": {"addr": {"type": "integer"}, "bytes": {"type": "array", "items": {"type": "integer"}}},
                      "required": ["addr", "bytes"]}},
    {"name": "conclude", "description": "Finish: one entry per address with what the device is, how sure you are, the evidence, "
     "and how to read its main value (register, length, formula).",
     "input_schema": {"type": "object", "properties": {"devices": {"type": "array", "items": {"type": "object", "properties": {
         "addr": {"type": "integer"}, "identity": {"type": "string"}, "confidence": {"type": "number"},
         "evidence": {"type": "string"}, "main_value": {"type": "string"}, "read_recipe": {"type": "string"},
         "current_reading": {"type": "string"}}, "required": ["addr", "identity", "confidence", "evidence"]}}}, "required": ["devices"]}},
]

SYSTEM = """You are incubating unknown hardware on a bare-metal device (POKE). You have an I2C bus service on it: scan, read, write. Nobody will tell you what is attached.

You also have the project's own knowledge, read the RouteMind way: knowledge_table() lists areas with one 'use when' line each; pick the area that fits, knowledge_table(its path) lists its nodes, knowledge_read(path) gives one node's text. Read before you probe blind — earlier incubations left identification recipes and notes on how this device's models behave — but trust the bus over the notes when they disagree, and say so.

Work like an engineer at a bench: scan, then for each address use what you know about common I2C parts at that address — ID / WHO_AM_I registers, register layouts, value ranges, BCD patterns, defaults — and read whatever confirms or refutes a hypothesis. Prefer reads. A write is allowed only when a read cannot settle it, and only to registers you already read (so you can restore them). Keep going until every address is identified or you have exhausted reasonable hypotheses, then call conclude. Report readings in physical units when you can (temperature in °C, time as hh:mm:ss, etc.)."""

def main():
    twin = pokeedge.Twin(extra=VIRTUAL, monitor=True) if HOST == "twin" else None
    host = twin.__enter__() if twin else HOST
    try:
        link = pokeedge.connect(host)
        if twin:
            twin.monitor(f"qom-set t1 temperature {TRUTH_TEMP_MDEG}")
        res = open(os.path.join(ROOT, "edge/library/pi4/i2c/resident.bin"), "rb").read()
        dj = json.load(open(os.path.join(ROOT, "edge/library/pi4/i2c/device.json")))
        maps = [(int(m["pa"], 16), int(m["len"], 16), m["attr"]) for m in dj["mappings"]]
        info = json.loads(link.request(b"INFO", timeout=2))
        if info.get("resident") != "i2c":
            if info.get("resident"):
                link.request(b"RSTP", timeout=5)
            print("I2C bus service:", pokeedge.run_resident(link, res, maps, "i2c"))

        def run_tool(name, inp):
            try:
                return run_tool_(name, inp)
            except Exception as e:                           # a bad call is the model's problem to fix, not a crash
                return {"error": f"{type(e).__name__}: {e}"}
        def run_tool_(name, inp):
            if name == "i2c_scan":
                st, d = pokeedge.sreq(link, 1)
                return {"addresses": [a for a in d[1:1 + d[0]]]}
            if name == "i2c_read":
                reg = inp.get("reg"); reg = 0xFF if reg is None else int(reg)
                st, d = pokeedge.sreq(link, 2, bytes([int(inp["addr"]), reg, max(1, min(32, int(inp.get("n", 1))))]))
                return {"status": "ok" if st == 0 else ("no ACK" if st == 2 else f"error {st}"), "bytes": list(d) if st == 0 else []}
            if name == "i2c_write":
                st, _ = pokeedge.sreq(link, 3, bytes([int(inp["addr"])] + [int(b) & 0xFF for b in inp["bytes"]]))
                return {"status": "ok" if st == 0 else ("no ACK" if st == 2 else f"error {st}")}
            return {"error": "unknown tool"}

        client = anthropic.Anthropic()
        ktools, krun = routemind.knowledge_tools() if (routemind.available() and "--no-knowledge" not in sys.argv) else ([], None)
        print("[지식]", "RouteMind 온톨로지 연결됨 (knowledge_table / knowledge_read)" if ktools else "온톨로지 없음 — 버스만으로")
        tools_all = TOOLS + ktools
        messages = [{"role": "user", "content": "An unknown set of devices has just been attached to this POKE edge's I2C bus. Find out what they are."}]
        conclusion, calls, t0 = None, 0, time.time()
        print(f"\n[인큐베이팅] {MODEL}에게 버스 서비스만 주고 시작 — 장치 정보는 주지 않음\n")
        for turn in range(40):
            msg = client.messages.create(model=MODEL, max_tokens=4000, system=SYSTEM, tools=tools_all, messages=messages)
            messages.append({"role": "assistant", "content": msg.content})
            results = []
            for block in msg.content:
                if block.type == "text" and block.text.strip():
                    print("  💭", block.text.strip().replace("\n", "\n     ")[:600])
                elif block.type == "tool_use":
                    calls += 1
                    if block.name == "conclude":
                        conclusion = block.input; results.append({"type": "tool_result", "tool_use_id": block.id, "content": "recorded"})
                        continue
                    if block.name.startswith("knowledge_"):
                        out = krun(block.name, block.input); kcalls = locals().get("kcalls", 0) + 1
                        print(f"  🧭 {block.name}({block.input.get('path') or 'hop 0'}) → {len(out)} chars")
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": out}); continue
                    out = run_tool(block.name, block.input)
                    arg = ", ".join(f"{k}={hex(v) if k == 'addr' else v}" for k, v in block.input.items())
                    shown = out.get("addresses") and [hex(a) for a in out["addresses"]] or (out.get("bytes") and bytes(out["bytes"]).hex()) or out.get("status") or out.get("error")
                    print(f"  🔧 {block.name}({arg}) → {shown}")
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(out)})
            if conclusion:
                break
            if not results:                                   # a thinking-only turn: nudge it onward
                if msg.stop_reason == "end_turn" and turn > 0:
                    results = [{"type": "text", "text": "Continue probing, or call conclude with what you know."}]
                else:
                    results = [{"type": "text", "text": "Go on."}]
            messages.append({"role": "user", "content": results})

        print(f"\n[결론] {calls}번의 버스 접근, {time.time() - t0:.0f}초")
        os.makedirs(os.path.join(pokeedge.BUILD, "incubated"), exist_ok=True)
        truth = {0x49: "tmp105", 0x1e: "lsm303dlhc magnetometer", 0x68: "ds1338 rtc", 0x60: "pca9552 led controller"}
        for d in (conclusion or {}).get("devices", []):
            a = int(d["addr"])
            print(f"  0x{a:02x}: {d['identity']}  (확신 {d.get('confidence', 0):.0%})")
            print(f"        근거: {d.get('evidence', '')[:300]}")
            if d.get("current_reading"): print(f"        현재값: {d['current_reading']}")
            if d.get("read_recipe"): print(f"        읽는 법: {d['read_recipe'][:200]}")
            if twin:
                print(f"        정답: {truth.get(a, '?')}")
            json.dump({"addr": f"0x{a:02x}", **d, "bus": "BSC1 (GPIO 2/3)", "incubated_by": MODEL, "at": time.strftime("%Y-%m-%d %H:%M")},
                      open(os.path.join(pokeedge.BUILD, "incubated", f"0x{a:02x}.json"), "w"), indent=2, ensure_ascii=False)
        if twin:
            print(f"\n[정답 대조] 온도 센서는 {TRUTH_TEMP_MDEG / 1000:.2f} °C로 설정돼 있었음; RTC는 호스트 시각 {time.strftime('%H:%M:%S')} 근처")
    finally:
        if twin:
            twin.__exit__(None, None, None)

if __name__ == "__main__":
    main()
