#!/usr/bin/env python3
"""spec_measure — measure a part's capability specs on the twin instead of trusting the fragment's comments.

For each part with twin controls: one image reads every channel through the capability API (the same
generated header a plan's step gets); the hub sets the control to values that include fractions, boots,
and compares. → registry/parts/<part>.json  implements[*].measured = {points, max_abs_err, step_seen,
fraction_kept, verdict}. A part whose readings lose what its own fragment claims (e.g. 0.125 °C steps
reported as whole degrees) is marked, so a plan's spec check — and the person — see it.

    ../sim/body/.venv/bin/python spec_measure.py [part …]
"""
import os, sys, json, time, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import registry as R, bench

# the twin's model and controls for each part: QEMU model, and which control each channel reads
TWIN = {
    "tmp105":  ("tmp105", {"": "temperature"}),
    "tmp421":  ("tmp421", {"local": "temperature0", "remote": "temperature1"}),
    "emc1403": ("emc1413", {"internal": "temperature0", "remote1": "temperature1", "remote2": "temperature2"}),
}
POINTS = [0, 12312, 25062, 38500, 70750, 100125]           # m°C — fractions of 1/16, 1/8 and 1/2 degree

def measure(part):
    model, knobs = TWIN[part]
    P = R.parts()[part]
    b = next(x for x in P["implements"] if x["cap"] == "temperature.read")
    addr = P["default_addr"] if isinstance(P["default_addr"], str) else f"0x{P['default_addr']:02x}"
    node = {"id": f"spec-{part}", "place": "실험대", "arch": "aarch64-pi4", "boot": "twin", "abi": "rt.h/1", "buses": ["i2c1"],
            "limits": {"run_budget_ms": 20000}, "link": {"kind": "twin"}, "twin": [[model, "s1", addr, part, f"{part} 시험"]]}
    nfile = os.path.join(R.REG, "nodes", f"{node['id']}.json")
    json.dump(node, open(nfile, "w"), ensure_ascii=False)
    try:
        inst = [i for i in R.instances(node["id"]) if i["cap"] == "temperature.read"]
        body = "void op_main(void) {\n    long v;\n" + "".join(
            f'    if (cap_temperature_read({i["const"]}, &v)) {{ fail("read {i["channel"]}"); return; }} report_int("ch_{i["channel"] or "0"}", v);\n'
            for i in inst) + "}\n"
        img, _, _ = R.build_step(node["id"], [i["id"] for i in inst], body, f"spec-{part}")
        rows = {i["channel"]: [] for i in inst}
        for v in POINTS:
            st = {f"s1 {knobs[ch]}": v for ch in knobs}
            res = R.boot_step(node["id"], img, st).get("result") or {}
            for ch in rows:
                rows[ch].append((v, res.get(f"ch_{ch or '0'}")))
        out = {}
        for ch, pts in rows.items():
            errs = [abs(g - s) for s, g in pts if isinstance(g, int)]
            fr = [g % 1000 for s, g in pts if isinstance(g, int) and s % 1000]
            claimed = b.get("spec", {}).get("resolution")
            kept = any(x != 0 for x in fr)
            worst = max(errs) if errs else None
            verdict = ("ok" if worst is not None and (claimed is None or worst <= max(claimed, 1) * 1.01 + 1) else
                       "loses fractions" if not kept else "error above its resolution")
            out[ch or "0"] = {"points": pts, "max_abs_err_mC": worst, "fraction_kept": kept, "claimed_resolution": claimed, "verdict": verdict}
        b["measured"] = {"when": time.strftime("%Y-%m-%d %H:%M"), "on": "twin", "channels": out}
        json.dump(P, open(os.path.join(R.REG, "parts", f"{part}.json"), "w"), indent=1, ensure_ascii=False)
        return out
    finally:
        os.remove(nfile)

if __name__ == "__main__":
    for p in sys.argv[1:] or list(TWIN):
        for ch, m in measure(p).items():
            print(f"{p:8} {ch:9} max error {m['max_abs_err_mC']} m°C · claimed step {m['claimed_resolution']} · fraction kept {m['fraction_kept']} → {m['verdict']}")
