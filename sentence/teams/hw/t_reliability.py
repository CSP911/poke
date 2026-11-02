"""5차: how reliable is learning a new part? Each trial learns one part from nothing, through learn.py (the same
job the hub runs), on an ISOLATED copy of the fragment catalog and the incubated/ memory — the owner's catalog,
fragments, fn.json and records are checked unchanged at the end.

    cd sentence/teams/hw && ../../../sim/body/.venv/bin/python t_reliability.py [--budget 30] [--only tmp421,…]

Per trial: the part is removed from the isolated copy (header, catalog entry, manifest functions, record,
signature), so the scan finds it unknown; then learn.start(addr) and wait. Measured: learned or not, rounds,
seconds, model calls (agents included), power-ons, the identification, identify() kind, the V7 review's
permissions (write_regs / max_write / policy per function — compared across trials of the same part).
Stops starting new trials when the model-call budget is spent. Results: results/reliability.json.
"""
import hwcommon  # noqa: F401
import os, sys, json, time, shutil, hashlib, tempfile, re
HERE = os.path.dirname(os.path.abspath(__file__))
SENT = os.path.dirname(os.path.dirname(HERE))
OWNER_FRAGS = os.path.join(os.path.dirname(SENT), "edge", "sentence", "frags")
OWNER_INC = os.path.join(SENT, "incubated")
RES = os.path.join(HERE, "results"); os.makedirs(RES, exist_ok=True)

PLAN = [("tmp421", 0x4c, "ext", 3), ("pca9554", 0x20, "ext", 3), ("dps310", 0x77, "ext", 3),
        ("eeprom (negative control)", 0x50, "eeprom", 1), ("emc1413", 0x18, "emc", 1)]
BUDGET = int(sys.argv[sys.argv.index("--budget") + 1]) if "--budget" in sys.argv else 30
ONLY = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None

def digest(*dirs):
    h = hashlib.sha1()
    for d in dirs:
        for root, _, files in sorted(os.walk(d)):
            for f in sorted(files):
                if f.endswith(".tmp"): continue
                p = os.path.join(root, f); h.update(p.encode()); h.update(open(p, "rb").read())
    return h.hexdigest()

def isolate(part):
    """A private copy of frags/ and incubated/ without `part` (if it is known there)."""
    d = tempfile.mkdtemp(prefix=f"hw-rel-{re.sub(r'[^a-z0-9]', '', part)[:10]}-")
    fr, inc = os.path.join(d, "frags"), os.path.join(d, "incubated")
    shutil.copytree(OWNER_FRAGS, fr, ignore=shutil.ignore_patterns("*.tmp"))
    shutil.copytree(OWNER_INC, inc, ignore=shutil.ignore_patterns("*.tmp", "failed"))
    cat = json.load(open(os.path.join(fr, "catalog.json")))
    if part in cat:
        cat.pop(part)
        json.dump(cat, open(os.path.join(fr, "catalog.json"), "w"), indent=2, ensure_ascii=False)
        for f in (os.path.join(fr, f"{part}.h"), os.path.join(inc, f"{part}.json")):
            if os.path.exists(f): os.remove(f)
        fn = json.load(open(os.path.join(inc, "fn.json")))
        for k in [k for k, v in fn["FN"].items() if v.get("frag") == part]:
            fn["FN"].pop(k); fn["POLICY"].pop(k, None)
        json.dump(fn, open(os.path.join(inc, "fn.json"), "w"), indent=1, ensure_ascii=False)
        sp = os.path.join(inc, "signatures.json")
        if os.path.exists(sp):
            sg = json.load(open(sp)); sg.pop(part, None); json.dump(sg, open(sp, "w"), indent=1)
    return d, fr, inc

def trial(part, addr, bench_name):
    d, fr, inc = isolate(part.split()[0])
    os.environ.update({"POKE_FRAGS": fr, "POKE_INCUBATED": inc, "POKE_BENCH": bench_name})
    import learn
    t0 = time.time()
    job = learn.start(addr, bench_name=bench_name)
    st = learn.wait(job["id"], timeout_s=2400, every_s=3)
    ev = learn.events(job["id"])
    cost = next((e.get("cost") for e in reversed(ev) if e.get("stage") == "done"), None) or {}
    ident = next((e for e in ev if e.get("stage") == "identify" and e.get("state") == "done"), {})
    rounds = len([e for e in ev if e.get("stage") == "fragment"])
    fails = [e for e in ev if e.get("stage") == "verify" and e.get("state") == "fail"]
    reg = (st.get("result") or {}).get("registered") or []
    out = {"part": part, "addr": f"0x{addr:02x}", "job": job["id"], "learned": bool(reg), "name": reg[0] if reg else None,
           "state": st["state"], "seconds": round(time.time() - t0), "rounds": rounds,
           "model_calls": cost.get("model_calls"), "agent_calls": cost.get("agent_calls"), "power_ons": cost.get("power_ons"),
           "identified_as": [(v.get("part"), v.get("confidence")) for v in ident.get("verdict", [])],
           "verify_failures": [p for e in fails for p in e.get("problems", [])][:4]}
    if reg:
        rec = json.load(open(os.path.join(inc, reg[0] + ".json")))
        fn = json.load(open(os.path.join(inc, "fn.json")))
        out["identify"] = rec.get("identify_kind")
        out["verification_kind"] = rec.get("verification_kind")
        out["permissions"] = {k: [v["access"], v["write_regs"], v["max_write"], fn["POLICY"].get(k)]
                              for k, v in sorted(fn["FN"].items()) if v.get("frag") == reg[0]}
        out["functions"] = [f.get("sig") for f in rec.get("functions", [])]
    else:
        fp = os.path.join(inc, "failed", f"0x{addr:02x}.json")
        out["not_learned_record"] = os.path.exists(fp)
        out["why"] = (st.get("result") or {}).get("why") or (out["verify_failures"][:1] or [st.get("log_tail", "")[-300:]])[0]
    shutil.rmtree(d, ignore_errors=True)
    return out

def main():
    before = digest(OWNER_FRAGS, OWNER_INC)
    rows, used = [], 0
    path = os.path.join(RES, "reliability.json")
    for part, addr, bname, n in PLAN:
        if ONLY and part.split()[0] not in ONLY: continue
        for i in range(n):
            if used >= BUDGET:
                rows.append({"part": part, "skipped": f"model-call budget {BUDGET} spent"}); break
            print(time.strftime("%H:%M:%S"), f"trial {part} #{i + 1}", flush=True)
            r = trial(part, addr, bname)
            used += r.get("model_calls") or 0
            rows.append(r)
            print("   ", json.dumps({k: r.get(k) for k in ("learned", "name", "rounds", "seconds", "model_calls", "power_ons", "identify",
                                                         "identified_as", "why")}, ensure_ascii=False), flush=True)
            json.dump({"rows": rows, "model_calls": used}, open(path, "w"), indent=1, ensure_ascii=False)
    after = digest(OWNER_FRAGS, OWNER_INC)
    json.dump({"rows": rows, "model_calls": used, "owner_unchanged": before == after}, open(path, "w"), indent=1, ensure_ascii=False)
    print("owner catalog/fragments/incubated unchanged:", before == after, "— model calls:", used)

if __name__ == "__main__":
    main()
