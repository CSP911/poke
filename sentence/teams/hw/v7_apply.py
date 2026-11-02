"""V7 for the parts learned before the review existed (실기기팀, at the verify team's request).

    cd sentence/teams/hw && ../../../sim/body/.venv/bin/python v7_apply.py [--dry]

1 DPS310 "cold start": the twin's DPS310 is already configured at power-on, so the fragment's configuration
  writes (0x06–0x08, made only when the part is not configured) were never seen — the review would strip
  them and a real chip's first reading would be refused. The hub clears the configuration first (its own
  writes), then runs the recorded test op; the fragment's own writes to 0x06–0x08 after the clear are the
  evidence. That run joins the record's verification (test "cold start (hub)").
2 say.incubation_review(rec) for every learned part (records now carry the runs' POKE-EFFECTS); the
  reviewed write registers, max_write and policy go to incubated/fn.json (key_lock, whole-file replace);
  the review table goes into each record (rec["review"]).
SSD0303: its identify() returns -1 (a write-only controller has nothing to read), so the identification
confidence (0.70) cannot be raised — the review's deny of its two write functions stands.
"""
import hwcommon  # noqa: F401
import os, sys, json, re
os.environ["POKE_BENCH"] = "ext"
import say, bench
import incubation as INC

DRY = "--dry" in sys.argv

def cold_start_dps310():
    rec = json.load(open(os.path.join(INC.INC, "dps310.json")))
    addr = int(rec["addr"], 16)
    op = rec["test_ops"][0]
    # PRS_CFG 0, TMP_CFG 0, MEAS_CFG = coefficients and sensor ready (0xC0), no mode, no results: a chip just out
    # of reset. (0x00 in MEAS_CFG would also clear the twin's ready flags, which a real chip keeps read-only —
    # that would be a broken chip, not a cold one: the first try did that and the fragment rightly gave up.)
    clear = ("static void hub_clear(void) {  /* hub, not the fragment: the part as a cold chip has it */\n"
             "    static const i2c_u8 z6[2] = {0x06, 0x00}, z7[2] = {0x07, 0x00}, z8[2] = {0x08, 0xC0};\n"
             f"    i2c_write({addr:#x}, z6, 2); i2c_write({addr:#x}, z7, 2); i2c_write({addr:#x}, z8, 2);\n}}\n")
    src = op.replace("void op_main(void)\n{", clear + "void op_main(void)\n{\n    hub_clear();", 1)
    assert src != op, "test op layout changed"
    regs = sorted({r for f in rec["functions"] for r in f.get("write_regs", [])} | {6, 7, 8})
    allow = {"bus": "i2c1", "devices": [{"addr": addr, "read": True, "pointer": True, "write_regs": regs, "max_write": 2}]}
    data, obj, img = say.build(src, "hw-dps310-cold", allow=allow)
    b = bench.boot(img)
    fx = b.get("effects") or say.parse_effects(b.get("lines", []))
    xs = [x for x in (fx or {}).get("x", []) if x[2] == addr and x[1] == "w" and x[4] >= 2]
    hub = xs[:3]
    after = [x for x in xs[3:] if int(x[3][:2], 16) in (6, 7, 8)]
    res = b.get("result") or {}
    ok = len(hub) == 3 and len(after) >= 1 and not res.get("error") and res.get("press_pa")
    print("cold start:", "PASS" if ok else "FAIL", "— fragment wrote", sorted({x[3][:2] for x in after}), "after the clear; result", res)
    rec["verification"] = [v for v in rec["verification"] if v.get("test") != "cold start (hub)"] + [
        {"test": rec["tests"][0]["name"], "note": "cold start (hub): configuration cleared before the recorded op",
         "ok": bool(ok), "draws": [{"draw": {"cold": True}, "result": res, "effects": fx, "lines": b["lines"][-6:]}]}]
    return rec, ok

def main():
    cold, ok = cold_start_dps310()
    names = [f[:-5] for f in os.listdir(INC.INC) if f.endswith(".json") and f != "fn.json" and f != "signatures.json"]
    with say.key_lock("hub-catalog"):
        fns = json.load(open(INC.FN_JSON))
        for n in sorted(names):
            rec = cold if n == "dps310" else json.load(open(os.path.join(INC.INC, n + ".json")))
            if n == "dps310" and not ok:
                # the twin's DPS310 model never raises the result-ready flags after a command, so a cold chip's
                # first measurement cannot finish on the twin: the fragment's config writes are seen (0x07, 0x08)
                # but the run ends in its own "raw read failed". Review not applied — declared access kept —
                # until a real chip (or a better model) completes the cold path. Recorded, not hidden.
                cold["review_pending"] = ("cold start on the twin: the fragment configured the part (writes seen after "
                                          "the hub's clear) but the twin never reports a finished measurement; review "
                                          "of dps310's write registers waits for a real chip")
                if not DRY: say.write_json_atomic(os.path.join(INC.INC, "dps310.json"), cold, indent=1, ensure_ascii=False, default=str)
                print("dps310: cold start incomplete on the twin — review not applied, declared access kept, noted in the record"); continue
            rv = say.incubation_review(rec)
            for f in rv.get("functions", []):
                fn = re.search(r"\b(\w+)\s*\(", f["sig"]).group(1)
                if fn not in fns["FN"]: continue
                old = (fns["FN"][fn]["write_regs"], fns["FN"][fn]["max_write"], fns["POLICY"].get(fn))
                fns["FN"][fn]["write_regs"] = sorted(f.get("write_regs", [])); fns["FN"][fn]["max_write"] = int(f.get("max_write", 1))
                fns["POLICY"][fn] = f.get("policy") or fns["POLICY"].get(fn)
                new = (fns["FN"][fn]["write_regs"], fns["FN"][fn]["max_write"], fns["POLICY"][fn])
                print(f"{n:8} {fn:22} {old} → {new}" + ("" if old == new else "   ← changed"))
            rec["review"] = rv.get("rows"); rec["review_problems"] = rv.get("problems")
            if not DRY: say.write_json_atomic(os.path.join(INC.INC, n + ".json"), rec, indent=1, ensure_ascii=False, default=str)
        if not DRY: say.write_json_atomic(INC.FN_JSON, fns, indent=1, ensure_ascii=False)
    print("applied" if not DRY else "dry run — nothing written")

if __name__ == "__main__":
    main()
