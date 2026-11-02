"""H3/H4 test: rt_power.c (watchdog semantics, RCFG, budget, scrub, reset partition, CDIV) on the twin.

Builds into teams/hw/results/build (never touches the shared build/rt objects), against the mainline
runtime (edge/sentence: rt.c with the rt_power hooks, applied 2026-10-11). A control boot links the
same op with rt_power_off's scrub skipped (a copy of rt.c whose power_off resets directly) to show the
scrub is what clears the op's bytes.

    ../../../sim/body/.venv/bin/python t_power.py

Each case boots its own QEMU (raspi4b + the bench), waits for the node to power off, then — VM paused —
reads PM_RSTS and BSC1 DIV through the monitor and dumps the op's pages + runtime bss with pmemsave.
"""
import os, sys, json, time, socket, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
SENT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, SENT)
import say, rtcfg

RES = os.path.join(HERE, "results"); os.makedirs(RES, exist_ok=True)
say.BUILD = os.path.join(RES, "build")
EDGE = say.EDGE
RT = os.path.join(EDGE, "rt.c")
RUNTIME_SRC = say.RUNTIME_SRC = [(RT if o == "poke_rt_main.o" else src, o) for src, o in say.RUNTIME_SRC]   # mainline list, rt.c by path
say.RUNTIME_DEPS = tuple(say.RUNTIME_DEPS) + ("rt_power.h", "rt_power.c")
H = '#include "rt.h"\n'

def syms(elf):
    out = {}
    for l in subprocess.run(["aarch64-elf-nm", elf], capture_output=True, text=True).stdout.splitlines():
        p = l.split()
        if len(p) == 3: out[p[2]] = int(p[0], 16)
    return out

def hmp(mon, cmd):
    return mon.call("human-monitor-command", **{"command-line": cmd})

def xp(mon, addr):
    r = hmp(mon, f"xp /1wx {addr:#x}")
    return int(r.split(":")[1].split()[0], 16)

def boot_mem(img, elf, budget_s=30):
    port = 20000 + int.from_bytes(os.urandom(2), "little") % 20000
    devs = []
    for model, qid, addr in say.BENCH:
        devs += ["-device", f"{model},id={qid},bus=i2c-bus.1,address={addr:#x}"]
    serial = os.path.join(RES, f"serial-{os.getpid()}-{port}.txt"); open(serial, "w").close()
    q = subprocess.Popen(["qemu-system-aarch64", "-M", "raspi4b", "-kernel", img, "-display", "none", "-S",
                          "-serial", f"file:{serial}", "-qmp", f"tcp:127.0.0.1:{port},server=on,wait=off", "-monitor", "none",
                          "-action", "reboot=shutdown,shutdown=pause", *devs], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    mon, out = None, {}
    try:
        mon = say.QMP(port)
        for k, v in say.default_bench().items():
            qid, prop = k.split(); mon.set(qid, prop, v)
        mon.call("cont"); t0 = time.time(); off = False
        while time.time() - t0 < budget_s:
            if "POKE-OFF" in open(serial, errors="replace").read(): off = True; break
            time.sleep(0.02)
        out["ms"] = round((time.time() - t0) * 1000)
        st = {}
        for _ in range(50):
            st = mon.call("query-status")
            if not st.get("running"): break
            time.sleep(0.02)
        out["vm"] = st.get("status"); out["off"] = off and not st.get("running")
        s = syms(elf)
        a, b = s["__rt_text_end"], s["__rt_stack_top"] - 0x4000
        dump = os.path.join(RES, f"mem-{port}.bin")
        mon.call("pmemsave", val=a, size=b - a, filename=dump)
        for _ in range(50):
            if os.path.exists(dump) and os.path.getsize(dump) == b - a: break
            time.sleep(0.02)
        mem = open(dump, "rb").read(); os.remove(dump)
        imgb = open(img, "rb").read()
        out["scrub"] = {"range": [hex(a), hex(b)], "bytes": b - a, "nonzero_after": sum(1 for x in mem if x),
                        "op_bytes_in_image": sum(1 for x in imgb[a - 0x80000: s["__op_x_end"] - 0x80000] if x)}
        out["pm_rsts"] = xp(mon, 0xFE100020)
        out["bsc1_div"] = xp(mon, 0xFE804014)
    finally:
        if mon:
            try: mon.call("quit")
            except Exception: pass
        try: q.wait(5)
        except subprocess.TimeoutExpired: q.kill(); q.wait()
    lines = open(serial, errors="replace").read().replace("\r", "").splitlines(); os.remove(serial)
    out["lines"] = lines
    out["result"], _ = say.parse_result(lines)
    return out

def build(src, tag, **cfg):
    data, o, img = say.build(src, tag)
    if cfg:
        data = rtcfg.patch(data, **cfg); open(img, "wb").write(data)
    return img, img[:-4] + ".elf", rtcfg.read(data)

TEMP = open(os.path.join(SENT, "build", "ops", "600ad0ab6d86.c")).read()
LOOP = H + 'void op_main(void){ report_int("started", 1); for(;;){} }'
DELAYS = H + 'void op_main(void){ report_int("started", 1); for(;;) delay_ms(100); }'

CASES = [
    ("temp, defaults (twin)", TEMP, {}, lambda r: r["result"].get("temperature_c") is not None and (r["pm_rsts"] & 0x555) == 0),
    ("temp, halt=1 → PM_RSTS partition 63", TEMP, {"halt": 1}, lambda r: (r["pm_rsts"] & 0x555) == 0x555 and r["result"].get("temperature_c") is not None),
    ("temp, cdiv=5000 (Pi value) reaches BSC1 DIV", TEMP, {"cdiv": 5000}, lambda r: r["bsc1_div"] == 5000 and r["result"].get("temperature_c") is not None),
    ("silent loop, budget 1500 ms", LOOP, {"budget_ms": 1500}, lambda r: r["result"].get("error") == "op ran past its time budget"),
    ("delay loop, budget 1500 ms (svc path)", DELAYS, {"budget_ms": 1500}, lambda r: r["result"].get("error") == "op ran past its time budget"),
    ("silent loop, wdog=2 quiet 1000 ms (Pi's quiet limit)", LOOP, {"wdog": 2, "quiet_ms": 1000},
     lambda r: (r["result"].get("error") or "").startswith("op went too long without a runtime call") and r["ms"] < 5000),
    ("delay loop, wdog=2 quiet 1000 ms — calls keep it alive until the budget", DELAYS, {"wdog": 2, "quiet_ms": 1000, "budget_ms": 3000},
     lambda r: r["result"].get("error") == "op ran past its time budget" and r["ms"] >= 2500),
]

def main():
    rows, ok = [], True
    for i, (name, src, cfg, judge) in enumerate(CASES):
        img, elf, c = build(src, f"hw-power-{i}", **cfg)
        r = boot_mem(img, elf)
        sc = r["scrub"]
        good = r["off"] and sc["nonzero_after"] == 0 and sc["op_bytes_in_image"] > 0 and judge(r)
        ok &= good
        row = {"case": name, "pass": good, "rcfg": {k: c[k] for k in ("wdog", "budget_ms", "cdiv", "quiet_ms", "halt")},
               "off": r["off"], "vm": r["vm"], "ms": r["ms"], "result": r["result"], "pm_rsts": hex(r["pm_rsts"]),
               "bsc1_div": r["bsc1_div"], "scrub": sc}
        rows.append(row); print("PASS" if good else "FAIL", json.dumps(row, ensure_ascii=False), flush=True)
    # control: power_off without the scrub (rt.c copy whose power_off resets directly) leaves the op's bytes in RAM
    src = open(RT).read()
    # the call into rt_power's power-off (after R1: scrub_stack_then(rtp_power_off); before: rtp_power_off();) → a direct reset
    import re
    noscrub = re.sub(r"^\s*(?:scrub_stack_then\(\s*rtp_power_off\s*\)|rtp_power_off\(\s*\))\s*;.*$",
                     "    R(PM + 0x24) = PM_PASS | 10; R(PM + 0x1c) = PM_PASS | (R(PM + 0x1c) & ~0x30u) | 0x20; for (;;) {}",
                     src, count=1, flags=re.M)
    if noscrub != src:
        ctl_rt = os.path.join(RES, "rt-noscrub.c"); open(ctl_rt, "w").write(noscrub)
        say.BUILD = os.path.join(RES, "build-control")
        say.RUNTIME_SRC = [(ctl_rt if o == "poke_rt_main.o" else f, o) for f, o in RUNTIME_SRC]
        img, elf, _ = build(TEMP, "hw-power-control")
        r = boot_mem(img, elf)
        ctl = r["off"] and r["scrub"]["nonzero_after"] > 0
        print("PASS" if ctl else "FAIL", "control (no scrub): bytes left in RAM after power-off:", r["scrub"]["nonzero_after"]); ok &= ctl
        say.BUILD = os.path.join(RES, "build"); say.RUNTIME_SRC = RUNTIME_SRC
    else:
        print("FAIL control: rt.c no longer calls rtp_power_off (directly or through scrub_stack_then)"); ok = False
    # static: a Pi-target image keeps the rules, and the twin target never arms the watchdog
    data = open(build(TEMP, "hw-power-pi")[0], "rb").read()
    pi, tw = rtcfg.read(rtcfg.for_target(data, "pi")), rtcfg.read(rtcfg.for_target(data, "twin"))
    st = pi["wdog"] == 1 and pi["cdiv"] == 5000 and pi["quiet_ms"] < 15000 and tw["wdog"] == 0 and not rtcfg.check(pi)
    try:
        rtcfg.patch(data, wdog=1, quiet_ms=16000); st = False
    except rtcfg.RcfgError:
        pass
    print("PASS" if st else "FAIL", "static: pi", pi, "twin", tw); ok &= st
    json.dump({"rows": rows, "static_pi": pi, "runtime": RT}, open(os.path.join(RES, "power.json"), "w"), indent=1, ensure_ascii=False)
    print("power", "PASS" if ok else "FAIL", f"{sum(r['pass'] for r in rows)}/{len(rows)} boots")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
