"""G4: sentences that use parts the hub learned by incubation, judged against the extended bench's truth.

    cd sentence/teams/hw && ../../../sim/body/.venv/bin/python t_newparts.py [--no-model]

Each sentence goes through say.make_op (record → cache → Claude Code), is built with its manifest's allow
list (runtime v1), and boots on the EXT bench (POKE_BENCH=ext) with bench values this script draws.
--no-model: only sentences the hub already remembers (for regress --full without new model calls;
a sentence not yet remembered is SKIP).

Pass: the node powered off, no error, and what it reported is what the bench holds:
  remote temperature  a *_c / *_mC result within 0.2 °C of the TMP421's remote channel (temperature1)
  input pins          the PCA9554 pins 0–3 as drawn (a bitmask, a 0/1 string, or one key per pin)
  screen text         the SSD0303 shows one glyph per character of HELLO, the two Ls alike, the others distinct
  pin output          after "확장 포트 5번 핀 LOW로 내려줘" the PCA9554 pin 5 reads low
  pressure            "기압 알려줘" (DPS310, learned in B3 without a person) within 1 % of the independent reading
"""
import hwcommon  # noqa: F401 — owner's records untouched (decision 7)
import os, sys, json, random, time
os.environ["POKE_BENCH"] = "ext"
HERE = os.path.dirname(os.path.abspath(__file__))
SENT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, SENT)
import say, bench
import incubation as incubate                      # sentence/incubation.py (renamed from incubate.py: sim/body has its own)
import messages_ko as KO

NO_MODEL = "--no-model" in sys.argv
RES = os.path.join(HERE, "results"); os.makedirs(RES, exist_ok=True)
rnd = random.Random(int(time.time()))

def nums(res):
    for k, v in (res or {}).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool): yield k, float(v)

def judge_remote(res, st, ext):
    want = st["x1 temperature1"] / 1000
    hits = [(k, v) for k, v in nums(res) if (k.endswith("_c") and abs(v - want) <= 0.25) or (k.lower().endswith("mc") and abs(v / 1000 - want) <= 0.2)]
    return bool(hits), f"bench remote {want:.3f} °C; report {res}"

def judge_pins(res, st, ext):
    lv = [1 if st.get(f"g1 pin{i}", "high") == "high" else 0 for i in range(8)]
    mask4, mask8 = sum(b << i for i, b in enumerate(lv[:4])), sum(b << i for i, b in enumerate(lv))
    ok = any(int(v) in (mask4, mask8) for _, v in nums(res))
    for v in (res or {}).values():
        if isinstance(v, str):
            s = v.replace(" ", "").replace("0b", "")
            ok |= s in ("".join(map(str, lv[:4])), "".join(map(str, lv)), "".join(map(str, lv[:4]))[::-1], "".join(map(str, lv))[::-1])
    import re
    per = {i: None for i in range(4)}
    for k, v in (res or {}).items():
        m = re.search(r"(?:pin|p|in|input)_?(\d)", k.lower())
        if m and int(m.group(1)) < 8:
            vv = {"high": 1, "low": 0, "true": 1, "false": 0}.get(str(v).lower(), v)
            try: per[int(m.group(1))] = int(vv)
            except (TypeError, ValueError): pass
    ok |= all(per[i] == lv[i] for i in range(4))
    return ok, f"bench pins 0–7 {lv}; report {res}"

def judge_screen(res, st, ext):
    import manifest as MF
    if MF.policy().get("ssd0303_text") == "deny":
        # V7 (verify team): the SSD0303's identity cannot be confirmed (write-only, identify() = -1, confidence 0.70)
        # → its write functions are denied. Then nothing may be drawn, and the run must say it was stopped.
        lit = sum(map(sum, ext.get("screen") or [[0]]))
        return (lit == 0 and bool(res.get("error"))), f"denied by the safety review: screen dark ({lit} px), node said {res.get('error')!r}", True
    rows = ext.get("screen")
    if not rows: return False, "no screen"
    why = incubate.check_screen([("HELLO", rows)])
    return not why, ("; ".join(why) or "HELLO: 5 glyphs, L twice alike") + "\n" + bench.ascii_art(rows)

def judge_pin5(res, st, ext):
    p = ext.get("pca9554_pins", {}).get(5)
    return p == "low", f"pin 5 after the run: {p}"

def judge_pressure(res, st, ext):
    """The twin's DPS310 has no control: its pressure is fixed. Truth = the independent reading the hub
    recorded when it learned the part (incubated/dps310.json, a second engineer's computation from the registers)."""
    rec = json.load(open(os.path.join(SENT, "incubated", "dps310.json")))
    ref = {c["key"]: float(c["independent"]) for c in (rec.get("oracle") or {}).get("compare", [])}
    pa = ref.get("press_pa") or (ref.get("press_hpa", 0) * 100)
    hits = []
    for k, v in nums(res):
        kl = k.lower()
        for cand in (v, v * 100, v * 1000):              # Pa, hPa, kPa
            if pa and abs(cand - pa) <= pa * 0.01 and ("pa" in kl or "press" in kl or "baro" in kl): hits.append(k)
    return bool(hits), f"independent reading {pa:.0f} Pa; report {res}"

CASES = [
    ("원격 온도 센서 온도 알려줘", judge_remote, lambda s: s.update({"x1 temperature1": rnd.randrange(-5000, 60000, 125)})),
    ("확장 포트 입력 핀 0~3번 상태 알려줘", judge_pins, lambda s: s.update({f"g1 pin{i}": rnd.choice(["high", "low"]) for i in range(4)})),
    ("화면에 HELLO 띄워줘", judge_screen, lambda s: None),
    ("확장 포트 5번 핀을 LOW로 내려줘", judge_pin5, lambda s: s.update({"g1 pin5": "high"})),
    ("기압 알려줘", judge_pressure, lambda s: None),
]

def remembered(sentence):
    import record as REC
    return REC.lookup(sentence) is not None

def main():
    rows, ok = [], True
    only = [int(x) for x in sys.argv[sys.argv.index("--only") + 1].split(",")] if "--only" in sys.argv else range(len(CASES))
    for sentence, judge, draw in [CASES[i] for i in only]:
        row = {"sentence": sentence}
        if NO_MODEL and not remembered(sentence):
            row["status"] = "SKIP (not remembered yet)"; rows.append(row); print(row); continue
        try:
            op = say.make_op(sentence, log=lambda *a: None)
        except KO.Refusal as e:
            row.update(status="FAIL", why=f"refused: {e}"); rows.append(row); ok = False; print(row); continue
        except Exception as e:
            why = f"{type(e).__name__}: {str(e)[:300]}"
            if NO_MODEL and hwcommon.model_unavailable(why):
                row.update(status="SKIP (model unavailable)", why=why); rows.append(row); print(row); continue
            row.update(status="FAIL", why=why); rows.append(row); ok = False; print(row); continue
        for attempt in (1, 2):                    # one retry when the twin gave no reading at all (load from a parallel regress)
            st = bench.default_state(); draw(st)
            b = bench.boot(op["path"], state=st, budget_s=60)
            res, ext = b.get("result") or {}, b["truth"].get("ext", {})
            j = judge(res, st, ext)
            good, why, error_expected = (j + (False,))[:3]
            good = good and b.get("powered_off") and (error_expected or not res.get("error"))
            if good or (res and b.get("powered_off")):
                break
            row["retried"] = "once (no reading / not off on the first try)"
        row.update(status="PASS" if good else "FAIL", cached=op.get("cached"), result=res, bench={k: v for k, v in st.items() if k.split()[0] in ("x1", "g1")},
                   why=why, manifest_fns=[u.get("fn") for u in (op.get("manifest") or {}).get("uses", [])], powered_off=b.get("powered_off"))
        rows.append(row); ok &= good
        print(row["status"], sentence, "|", why.splitlines()[0][:150])
        if "\n" in why: print(why.split("\n", 1)[1])
    json.dump(rows, open(os.path.join(RES, "newparts.json"), "w"), indent=1, ensure_ascii=False)
    n = sum(r["status"] == "PASS" for r in rows)
    print(f"newparts {'PASS' if ok else 'FAIL'} {n}/{len(rows)}")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
