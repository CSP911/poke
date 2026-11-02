"""AI incubation with Claude Code as the incubating mind — no Anthropic API.

Stage 1 (identify): Claude Code runs as an agent whose ONLY permitted tool is
`bus.py` (scan / read / write on the edge's I2C bus service, and the project's
knowledge map). It probes the unknown devices and ends with a JSON verdict.
Stage 2+3 (write the driver, verify, register): incubate2 with its language
model swapped for `claude -p` (claude_cli.Client), feedback rounds continuing
the same Claude Code session.

    .venv/bin/python incubate_cc.py [--register]

On the QEMU twin with four undisclosed virtual devices. Results go to
build/cc-run/ (the API-era results in build/incubated are left alone);
drivers join edge/library only with --register.
"""
import os, re, sys, json, time, glob, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pokeedge, claude_cli

RUN = os.path.join(HERE, "build", "cc-run")
TRUTH_TEMP_MDEG = 19750
VIRTUAL = ["-device", "tmp105,id=t1,bus=i2c-bus.1,address=0x4a",
           "-device", "lsm303dlhc_mag,id=m1,bus=i2c-bus.1,address=0x1e",
           "-device", "ds1338,bus=i2c-bus.1,address=0x68",
           "-device", "pca9552,id=l1,bus=i2c-bus.1,address=0x61"]
TRUTH = {0x4a: "tmp105 temperature sensor", 0x1e: "lsm303dlhc magnetometer (HMC5883-compatible map)",
         0x68: "ds1338 RTC (DS1307-compatible)", 0x61: "pca9552 LED controller"}

BRIEF = """You are incubating unknown hardware on a bare-metal device (POKE, no operating system). Nobody will tell you what is attached to its I2C bus. Your only instrument is this command, run from the current directory:

  .venv/bin/python bus.py scan
  .venv/bin/python bus.py read ADDR [REG] N        (REG omitted = read without writing a register pointer)
  .venv/bin/python bus.py write ADDR BYTE [BYTE …]
  .venv/bin/python bus.py knowledge [PATH]          (the project's knowledge map, read the RouteMind way: no PATH = list of areas with a 'use when' line each; then an area's path; then knowledge-read a node)
  .venv/bin/python bus.py knowledge-read PATH

Work like an engineer at a bench: read the knowledge map first — earlier incubations left identification recipes and notes on how these device models behave, but trust the bus over the notes, and an entry marked OPEN QUESTION is not knowledge. Then scan, and for each address use what you know about common I2C parts at that address — ID registers, register layouts, value ranges, BCD patterns, auto-increment behaviour — reading whatever confirms or refutes a hypothesis. Prefer reads; write only to a register you have already read, and only when a read cannot settle it. Report readings in physical units.

When done, end your reply with exactly one fenced block:
```json
[{"addr": "0x..", "identity": "...", "confidence": 0.0-1.0, "evidence": "the reads that decided it", "read_recipe": "how to read its main value", "current_reading": "..."}]
```"""

def stage1(host):
    os.makedirs(os.path.join(RUN, "incubated"), exist_ok=True)
    os.makedirs(os.path.join(HERE, "build", "cc"), exist_ok=True)
    open(os.path.join(HERE, "build", "cc", "edge.txt"), "w").write(host)
    open(os.path.join(HERE, "build", "cc", "bus-log.jsonl"), "w").close()
    print("[1단계] Claude Code가 에이전트로 버스를 더듬는 중 — 허용 도구: bus.py 하나", flush=True)
    t0 = time.time()
    out = claude_cli.agent("Find out what is attached to this POKE edge's I2C bus.",
                           ["Bash(.venv/bin/python bus.py:*)"], system=BRIEF, cwd=HERE, timeout=1800)
    dt = time.time() - t0
    text = out.get("result", "")
    calls = [json.loads(l) for l in open(os.path.join(HERE, "build", "cc", "bus-log.jsonl")) if l.strip()]
    for c in calls:
        what = c["op"] + (f" 0x{c['addr']:02x}" if "addr" in c else "") + (f" reg {c['reg']}" if c.get("reg") is not None else "") + \
               (f" n={c['n']}" if "n" in c else "") + (f" {c.get('path') or 'hop 0'}" if c["op"].startswith("knowledge") else "")
        print(f"  🔧 {what} → {str(c.get('out', str(c.get('chars', '')) + ' chars'))[:60]}")
    m = re.findall(r"```json\s*(\[.*?\])\s*```", text, re.S)
    verdict = json.loads(m[-1]) if m else []
    bus = sum(1 for c in calls if not c["op"].startswith("knowledge"))
    print(f"\n[결론] 버스 접근 {bus}회 + 지식 {len(calls) - bus}회, {dt:.0f}초, 비용 표시 ${out.get('total_cost_usd', 0):.2f} (구독 로그인)")
    for d in verdict:
        a = int(str(d["addr"]), 16) if not isinstance(d["addr"], int) else d["addr"]
        print(f"  0x{a:02x}: {d['identity'][:80]}  ({float(d.get('confidence', 0)):.0%})  — 정답: {TRUTH.get(a, '?')}")
        if d.get("current_reading"): print(f"        현재값: {str(d['current_reading'])[:100]}")
        json.dump({**d, "addr": f"0x{a:02x}", "incubated_by": "claude-code-cli", "at": time.strftime("%Y-%m-%d %H:%M")},
                  open(os.path.join(RUN, "incubated", f"0x{a:02x}.json"), "w"), indent=2, ensure_ascii=False)
    json.dump({"seconds": round(dt), "bus_calls": bus, "knowledge_calls": len(calls) - bus, "verdict": verdict, "transcript_tail": text[-3000:]},
              open(os.path.join(RUN, "stage1.json"), "w"), indent=1, ensure_ascii=False)
    print(f"[정답 대조] 온도 센서는 {TRUTH_TEMP_MDEG / 1000:.2f} °C로 설정돼 있었음")
    return verdict

def main():
    if "--stage2-only" not in sys.argv:                   # stage 1 needs its own twin; stage 2 boots another
        twin = pokeedge.Twin(extra=VIRTUAL, monitor=True); host = twin.__enter__()
        try:
            link = pokeedge.connect(host)
            twin.monitor(f"qom-set t1 temperature {TRUTH_TEMP_MDEG}")
            dj = json.load(open(os.path.join(pokeedge.ROOT, "edge/library/pi4/i2c/device.json")))
            maps = [(int(m["pa"], 16), int(m["len"], 16), m["attr"]) for m in dj["mappings"]]
            print("I2C 버스 서비스:", pokeedge.run_resident(link, open(os.path.join(pokeedge.ROOT, "edge/library/pi4/i2c/resident.bin"), "rb").read(), maps, "i2c"))
            link.sock.close()
            stage1(host)
        finally:
            twin.__exit__(None, None, None)
    # stage 2+3: the same incubate2, its model swapped for claude -p, outputs under build/cc-run
    print("\n[2단계] Claude Code가 드라이버를 쓰고 트윈에서 검증", flush=True)
    import incubate2
    pokeedge.BUILD = RUN
    incubate2.VIRTUAL = VIRTUAL
    sys.argv = [sys.argv[0], "--cli"] + ([] if "--register" in sys.argv else ["--no-register"])
    incubate2.main()

if __name__ == "__main__":
    main()
