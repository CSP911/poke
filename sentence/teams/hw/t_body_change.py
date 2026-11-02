"""B2: the body changes under a remembered sentence — "온도 알려줘" on three changed bodies.

    cd sentence/teams/hw && ../../../sim/body/.venv/bin/python t_body_change.py [--no-model] [--via-hub]

Every case runs in this process only: say.BENCH is changed here and say.WIRING points at a temporary
file, so the shared sentence/wiring.json and other teams' runs are untouched.

  moved   TMP105 moved 0x48 → 0x49   → the boot meets a silent part, the scan finds a part in the same
                                         address family with TMP105's register signature → wiring updated
                                         → the sentence runs again and reports the bench's temperature
  gone    TMP105 taken out           → "온도 센서(TMP105)가 몸에서 빠졌습니다 …", no retry
  new     a part nobody knows added  → the reading works as before, and the hub suggests incubating the
          (the first of dps310@0x77, emc1413@0x18, pca9535@0x21 that no fragment covers)

--via-hub: through hub.pipeline (needs the product team's hook; until then the reference flow
bodycheck.run_sentence is used). --no-model: a case that would need a new op (the moved body's first
pronunciation) is SKIP.
"""
import hwcommon  # noqa: F401 — owner's records untouched (decision 7)
import os, sys, json, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
SENT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, SENT)
os.environ["POKE_BENCH"] = "base"
import say, bench, bodycheck

RES = os.path.join(HERE, "results"); os.makedirs(RES, exist_ok=True)
S = "온도 알려줘"
BASE = list(bench.BASE)
VIA_HUB = "--via-hub" in sys.argv
if VIA_HUB: os.environ.setdefault("POKE_BODY_HINT_S", "0")   # the test wants the new-part notice every time

def fresh_wiring():
    hwcommon.reseed()                      # each case starts from the owner's memory, not the previous case's
    w = os.path.join(tempfile.mkdtemp(prefix="hw-body-"), "wiring.json")
    json.dump({}, open(w, "w")); say.WIRING = w
    return w

def temp_c(res):
    for k, v in (res or {}).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool) and k.endswith("_c"): return float(v)
    return None

def run(sentence):
    if VIA_HUB:
        import hub
        evs = list(hub.pipeline(sentence, yes=True))
        body = [e for e in evs if e.get("stage") == "body"]
        res = [e for e in evs if e.get("stage") == "result"]
        stop = next((e for e in evs if e.get("stage") == "stop"), None)
        return {"via": "hub", "body": body, "results": res, "stop": stop, "events": [e.get("stage") for e in evs]}
    return {"via": "bodycheck.run_sentence", **bodycheck.run_sentence(sentence, log=lambda s: print("   ", s), hint_age_s=0)}

def unknown_part():
    known = {int(str(e.get("default_addr")), 16) for e in json.load(open(os.path.join(say.FRAGS, "catalog.json"))).values()}
    # parts the hub has never learned (2026-11-02: emc1413 and pca9535 are now known — the latter by identify(), as a pca9555)
    for m, q, a in [("dps310", "p1", 0x77), ("emc1413", "e1", 0x18), ("max34451", "g3", 0x4e), ("m41t80", "g4", 0x6a)]:
        if a not in known: return (m, q, a)
    return None

def case_moved():
    fresh_wiring()
    say.BENCH = [(m, q, 0x49 if q == "t1" else a) for m, q, a in BASE]
    bodycheck.forget_body()
    r = run(S)
    if VIA_HUB:
        b = r["body"]; res = r["results"][-1] if r["results"] else {}
        w = json.load(open(say.WIRING))
        want = ((res.get("truth") or {}).get("temperature_mC") or 23750) / 1000
        got = temp_c(res.get("result"))
        # found by the boot (body event moved → rerun) or by the hub's first look at the body (wiring already fixed)
        ok = ((bool(b) and b[0].get("kind") == "moved") or w.get("tmp105") == ["0x49"]) and got is not None and abs(got - want) <= 0.1
        r["check"] = {"body": b[:1], "wiring": w, "temp_c": got, "events": r["events"]}
        return ok, r
    first, last = r["runs"][0], r["runs"][-1]
    want = (last.get("truth") or {}).get("temperature_mC")
    got = temp_c(last.get("result"))
    w = json.load(open(say.WIRING))
    found = (first.get("body") or {}).get("kind") == "moved" or "tmp105" in ((r.get("first_look") or {}).get("moved") or {})
    ok = (found and got is not None and want is not None and abs(got - want / 1000) <= 0.1 and w.get("tmp105") == ["0x49"])
    r["check"] = {"first": (first.get("body") or {}).get("text"), "temp_c": got, "bench_mC": want, "wiring": w,
                  "second_result": last.get("result"), "second_body": (last.get("body") or {}).get("text"),
                  "runs": len(r["runs"])}      # kept for diagnosis: one run of 5 (22:30) had no temperature on the second boot
    return ok, r

def case_gone():
    fresh_wiring()
    say.BENCH = [x for x in BASE if x[1] != "t1"]
    bodycheck.forget_body()                   # this body was seen before: start as if it had not been (the check must find it)
    r = run(S)
    if VIA_HUB:
        # either the boot meets the silent part (body event "gone" → stop body_gone), or — when an earlier body
        # check of this very body already found it gone — the hub answers without booting (stop cannot)
        st = r["stop"] or {}
        ok = (st.get("kind") == "body_gone" and ("빠" in (st.get("text") or "") or (r["body"] and r["body"][0].get("kind") == "gone"))) or \
             (st.get("kind") == "cannot" and "boot" not in r["events"])       # older hubs answered 'cannot' without booting
        r["check"] = {"stop": st.get("kind"), "text": st.get("text"), "body": r["body"][:1]}
        return ok, r
    first = r["runs"][0]
    v = first.get("body") or {}
    # either the boot met the silent part, or the first look at the body already found it gone (then the hub
    # answers without booting) — both must say plainly which part left
    ok = v.get("kind") == "gone" and len(r["runs"]) == 1 and "빠졌습니다" in (v.get("text") or "") and "TMP105" in (v.get("text") or "")
    r["check"] = {"text": v.get("text"), "hint": v.get("hint"), "first_look": r.get("first_look"), "refused": first.get("refused")}
    return ok, r

def case_new():
    fresh_wiring()
    part = unknown_part()
    if not part:
        return None, {"why": "every candidate part has a fragment already"}
    say.BENCH = BASE + [part]
    r = run(S)
    if VIA_HUB:
        ok = any(e.get("kind") == "new" for e in r["body"]) and r["results"] and temp_c(r["results"][-1].get("result")) is not None
        r["check"] = {"body": r["body"][:2], "stop": r["stop"], "result": r["results"][-1].get("result") if r["results"] else None}
        return ok, r
    first = r["runs"][0]
    v = first.get("body") or {}
    ok = (v.get("kind") == "new" and temp_c(first.get("result")) is not None and f"0x{part[2]:02x}" in v.get("text", "")
          and "incubation.py run" in v.get("hint", ""))
    r["check"] = {"part": part, "text": v.get("text"), "hint": v.get("hint"), "temp_c": temp_c(first.get("result"))}
    return ok, r

def case_learned_elsewhere():
    """H7: a part learned on the extended bench (DPS310), plugged into the default body: the catalog follows
    the parts this body really has, so the sentence works here too (before H7 it was hidden by the bench name)."""
    fresh_wiring()
    cat = json.load(open(os.path.join(say.FRAGS, "catalog.json")))
    if "dps310" not in cat:
        return None, {"why": "DPS310 not learned yet (incubation.py run on the ext bench)"}
    say.BENCH = BASE + [("dps310", "p1", 0x77)]
    listed = sorted(say.catalog())
    r = run("기압 알려줘")
    res = r["results"][-1].get("result") if VIA_HUB and r.get("results") else (r.get("runs") or [{}])[-1].get("result")
    p = next((float(v) for k, v in (res or {}).items() if isinstance(v, (int, float)) and "pa" in k.lower()), None)
    ok = "dps310" in listed and p is not None
    r["check"] = {"catalog": listed, "result": res}
    return ok, r

def case_moved_learned():
    """H9: a part learned by incubation, moved within its address family (TMP421 0x4c → 0x4d on the extended
    bench). It has no register signature (its stable registers read 00) — only its own identify() can say
    it is the same part. The body is checked the first time the hub looks at it (catalog), so the move is
    found before anything boots; the sentence then runs where the part is."""
    fresh_wiring()
    say.BENCH = [(m, q, 0x4d if q == "x1" else a) for m, q, a in bench.EXT]
    bodycheck.forget_body()
    listed = sorted(say.catalog())
    w = json.load(open(say.WIRING))
    st = bench.default_state(say.BENCH)
    r = run("원격 온도 센서 온도 알려줘")
    res = r["results"][-1].get("result") if VIA_HUB and r.get("results") else (r.get("runs") or [{}])[-1].get("result")
    want = st["x1 temperature1"] / 1000
    got = temp_c(res)
    ok = w.get("tmp421") == ["0x4d"] and "tmp421" in listed and got is not None and abs(got - want) <= 0.2
    r["check"] = {"wiring": w, "catalog_has_tmp421": "tmp421" in listed, "remote_c": got, "bench_c": want,
                  "stop": (r.get("stop") or {}).get("text") if VIA_HUB else None}
    return ok, r

def main():
    rows, ok = [], True
    for name, fn in (("moved", case_moved), ("gone", case_gone), ("new", case_new), ("learned-elsewhere", case_learned_elsewhere),
                     ("moved-learned", case_moved_learned)):
        t0 = time.time()
        try:
            good, r = fn()
            if good is False and not hwcommon.model_unavailable(r) and '"temp_c": null' in json.dumps(r.get("check") or {}, default=str):
                time.sleep(1.0); good, r = fn(); r["retried"] = "once (no reading on the first try — load)"   # a slow twin under a parallel regress
        except Exception as e:
            if "--no-model" in sys.argv and hwcommon.model_unavailable(f"{type(e).__name__}: {e}"):
                good, r = None, {"why": f"needs a model call: {e}"}
            else:
                good, r = False, {"error": f"{type(e).__name__}: {str(e)[:400]}"}
        if good is False and "--no-model" in sys.argv:
            if hwcommon.model_unavailable(r):
                good, r = None, {"why": "this body needs a new pronunciation (a model call) — not in --no-model", **{k: v for k, v in r.items() if k == "check"}}
        st = "SKIP" if good is None else ("PASS" if good else "FAIL")
        ok &= good is not False
        rows.append({"case": name, "status": st, "s": round(time.time() - t0, 1), **{k: v for k, v in r.items() if k in ("check", "why", "error", "via", "end")}})
        print(st, name, json.dumps(rows[-1].get("check") or rows[-1].get("why") or rows[-1].get("error"), ensure_ascii=False)[:400], flush=True)
    say.BENCH = BASE
    json.dump(rows, open(os.path.join(RES, "body_change.json"), "w"), indent=1, ensure_ascii=False, default=str)
    print("body change", "PASS" if ok else "FAIL", f"{sum(r['status'] == 'PASS' for r in rows)}/{len(rows)}")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
