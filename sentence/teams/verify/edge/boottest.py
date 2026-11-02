#!/usr/bin/env python3
"""Twin boot test for the v1 fence (verify team → test team). NOT run by the verify team:
the twin is the test team's. Run from sentence/:

    (cd teams/verify/edge && make)
    ../sim/body/.venv/bin/python teams/verify/edge/boottest.py            # fence cases, built against edge/sentence (mainline)
    ../sim/body/.venv/bin/python teams/verify/edge/boottest.py --proto    # the same cases from the prototype build
    ../sim/body/.venv/bin/python teams/verify/edge/boottest.py --hubops   # + the hub's cached ops, v1 vs v0

Each image is booted with say.boot() (no image check — the point is that the fence holds by itself).

If a case fails, what it most likely means:
  nothing after POKE-BOOT          the MMU switch-on itself (rt.c mmu_on); the runtime's own pages or UART not mapped
  ok_* fail with kind=8            a fragment touches a page the fence does not give (pt.h PT_DEV_PAGES)
  bad_loop_2s runs to the 30 s cap the timer interrupt never reached EL1 on the twin (GIC-400 PPI 30 / group
                                   setup in QEMU raspi4b) — the svc-time check still holds; report it to the verify team
  node_ms grows by more than ~2 ms the bss/stack zeroing or table build at boot (measure POKE-BOOT → op start)
Hub ops that read a clock or a time may differ between v1 and v0 for that reason alone."""
import os, sys, json, re
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))
import say

B = os.path.join(HERE, "build")
EC = lambda line: int(re.search(r"esr=0x([0-9a-f]+)", line).group(1), 16) >> 26 if "esr=" in line else None
CASES = [   # image, what must hold (b = say.boot() result, f = the POKE-FAULT line or "")
    ("ok_hello",      lambda b, f: b["result"] == {"answer": 42, "pi": 3.142} and not f),
    ("ok_tmp105",     lambda b, f: b["result"] == {"t": 23750} and not f),     # bench 23.75 °C, fragment sets 12 bits (D11)
    ("ok_wait",       lambda b, f: 1450 <= (b["result"] or {}).get("waited_ms", 0) <= 1700 and not f),
    ("bad_loop_2s",   lambda b, f: "kind=timeout" in f and "time budget" in (b["result"] or {}).get("error", "")),
    ("bad_null_say",  lambda b, f: "kind=4" in f and "pointer outside its pages" in (b["result"] or {}).get("error", "")),
    ("bad_null_read", lambda b, f: "kind=8" in f and EC(f) == 0x24 and "far=0x0000000000000000" in f),
    ("bad_recurse",   lambda b, f: "kind=8" in f and EC(f) == 0x24 and "not given" in (b["result"] or {}).get("error", "")),
    ("bad_bigresult", lambda b, f: (b["result"] or {}).get("truncated") is True and (b["result"] or {}).get("k0", "").startswith("0123")),
    ("bad_inband",    lambda b, f: b["result"] == {"x": 1} and sum(l.startswith("POKE-RESULT") for l in b["lines"]) == 1
                                   and sum(l.startswith("POKE-OFF") for l in b["lines"]) == 1),
    ("bad_longdelay", lambda b, f: "kind=delay" in f and "belong to the hub" in (b["result"] or {}).get("error", "") and "done" not in (b["result"] or {})),
    ("bad_gpio",      lambda b, f: "kind=8" in f and EC(f) == 0x24 and "far=0x00000000fe200004" in f and b["result"] is not None),
    ("bad_i2c_device", lambda b, f: "kind=i2c" in f and "not in its manifest" in (b["result"] or {}).get("error", "") and "after" not in (b["result"] or {})
                                    and any(d["a"] == 0x60 and d["denied"] == 1 for d in b["effects"]["dev"]) and b["truth"].get("leds_on") == []),
    ("bad_i2c_policy", lambda b, f: "kind=i2c" in f and "n=8" in f and "more bytes" in (b["result"] or {}).get("error", "")
                                    and any(d["a"] == 0x68 and d["denied"] == 1 and d["w"] == 1 for d in b["effects"]["dev"])),
    ("ok_manifest_led", lambda b, f: b["result"] == {"led": 4} and b["truth"].get("leds_on") == [4] and not f),
    # X4: the runtime API under bad arguments from an ordinary op — each ends safely (ok, fail or FAULT, then off)
    ("api_long_strings", lambda b, f: not f and len(next((l for l in b["lines"] if l.startswith("POKE-SAY ")), "")) == 9 + 199
                                      and any(len(k) == 63 and len(v) == 199 for k, v in (b["result"] or {}).items() if isinstance(v, str))
                                      and (b["result"] or {}).get("after") == 1),
    ("api_null_value",  lambda b, f: "kind=4" in f and "pointer outside its pages" in (b["result"] or {}).get("error", "")),
    ("api_unaligned",   lambda b, f: not f and (b["result"] or {}).get("xy") == "bcdef" and (b["result"] or {}).get("t2") == 23),
    ("api_page_cross",  lambda b, f: "kind=4" in f and "pointer outside its pages" in (b["result"] or {}).get("error", "")
                                     and not any(l.startswith("POKE-SAY") for l in b["lines"])),
    ("api_ro_buffer",   lambda b, f: "kind=4" in f and any(d["a"] == 0x48 and d["r"] >= 1 for d in (b.get("effects") or {}).get("dev", []))),
    ("api_neg_len",     lambda b, f: not f and {k: (b["result"] or {}).get(k) for k in ("r", "w", "x", "f1")} == {"r": 1, "w": 1, "x": 1, "f1": 12}
                                     and str((b["result"] or {}).get("f2")) in ("1.2e-17", "1.2E-17", "0.000000000000000012")
                                     and all(x[4] <= 64 for x in (b.get("effects") or {}).get("x", []))),
    ("api_neg_delay",   lambda b, f: "kind=delay" in f and (b["node_ms"] or 0) < 1000),
    ("bad_uart",      lambda b, f: "kind=8" in f and EC(f) == 0x24 and "far=0x00000000fe201000" in f
                                   and not any("A" == l.strip() for l in b["lines"])),
]

def build_mainline():
    """Build every case against the mainline runtime (edge/sentence) through say.build — the same path
    the hub uses, fence check included. bad_loop_2s gets RCFG budget_ms=2000 (rtcfg.patch)."""
    imgs = {}
    for name, _ in CASES:
        src_name = "bad_loop" if name == "bad_loop_2s" else name
        src = open(os.path.join(HERE, "tests", src_name + ".c")).read()
        if name != "bad_loop_2s":
            imgs[name] = say.build(src, "vt-" + name)[2]
            continue
        import rtcfg                                  # the budget is the image's RCFG field (rt_power.c)
        img = say.build(src, "vt-bad_loop_2s")[2]
        data = rtcfg.patch(open(img, "rb").read(), budget_ms=2000)   # read first: open(…, "wb") truncates
        open(img, "wb").write(data)
        imgs[name] = img
    return imgs

def main():
    from concurrent.futures import ThreadPoolExecutor
    proto = "--proto" in sys.argv                    # the verify team's prototype build (teams/verify/edge/build)
    imgs = {n: os.path.join(B, n + ".img") for n, _ in CASES} if proto else build_mainline()
    with ThreadPoolExecutor(4) as ex:                # twins may run side by side (one serial file per boot)
        boots = dict(zip(imgs, ex.map(lambda n: say.boot(imgs[n], say.default_bench(), budget_s=30), imgs)))
    good = 0
    for name, ok in CASES:
        b = boots[name]
        f = next((l for l in b["lines"] if l.startswith("POKE-FAULT")), "")
        fx = say.effects_report(b, imgs[name])               # X1 invariant: nothing outside the allow list reached the bus
        fx_ok = fx["ok"] is not None and not fx["undeclared"]
        right = b["powered_off"] and ok(b, f) and fx_ok
        good += right
        print(f"{'✓' if right else '✗'} {name:<14} off={b['powered_off']} node_ms={b['node_ms']} result={json.dumps(b['result'])[:90]} {f}"
              + ("" if fx_ok else f"  EFFECTS {json.dumps(fx)[:200]}"))
    print(f"\n{good}/{len(CASES)} fence cases as expected")
    if "--hubops" in sys.argv:                       # every cached hub op: same result under v1 as v0
        hd, v0d = os.path.join(B, "hubops"), os.path.join(say.BUILD, "img")
        same = n = 0
        for img in sorted(x for x in os.listdir(hd) if x.endswith(".img")):
            r1 = say.boot(os.path.join(hd, img), say.default_bench())
            r0 = say.boot(os.path.join(v0d, img), say.default_bench())
            n += 1; s_ = r1["result"] == r0["result"] and r1["powered_off"]; same += s_
            print(f"{'✓' if s_ else '✗'} {img:<18} v1 {r1['node_ms']} ms {json.dumps(r1['result'])}  v0 {r0['node_ms']} ms {json.dumps(r0['result'])}")
        print(f"\n{same}/{n} hub ops: v1 result == v0 result")
    return good == len(CASES)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
