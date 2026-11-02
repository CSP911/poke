"""T9 생성 변동: 쓰기·조건·반복 문장 12개를 빈 기록에서 3회씩 새로 생성(모델 약 36회) — 정확도, manifest 차이,
라운드 수, 같은 문장에서 나온 op들의 EFFECTS 집합 비교.
    cd sentence && ../sim/body/.venv/bin/python teams/test/e9_variance.py [--reps 3] [--limit N]
매 (문장, 회차)마다 새 빈 기록 디렉터리(record.RECORDS를 바꿈) — 같은 뜻 매칭·템플릿·옛 캐시가 끼지 않는 순수 생성."""
import os, sys, time, json, tempfile, statistics
HERE = os.path.dirname(os.path.abspath(__file__)); SENT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, SENT)
import isolate  # noqa: F401  (POKE_RECORDS → temporary; the owner's records are never touched)
os.environ["POKE_LEGACY_OPS"] = "0"; os.environ["POKE_INTENT_MODEL"] = "0"
os.chdir(SENT)
import say, record, claude_cli  # noqa: E402

CALLS = {"n": 0}
_ask = claude_cli.ask
def counted(*a, **k):
    CALLS["n"] += 1; return _ask(*a, **k)
claude_cli.ask = counted
MODES = {}
_get = say.QMP.get
def get_rec(self, qid, prop):
    v = _get(self, qid, prop); MODES[f"{qid} {prop}"] = v; return v
say.QMP.get = get_rec

def nums(r):
    return [v for v in (r or {}).values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
def near(r, v, tol): return any(abs(x - v) <= tol for x in nums(r))
L = lambda *on: {f"l1 led{i}": "on" for i in on}
CASES = [
    ("LED 6번 켜줘", {}, lambda r, t, b: t["leds_on"] == [6]),
    ("LED 2번이랑 9번 켜", {}, lambda r, t, b: t["leds_on"] == [2, 9]),
    ("LED 다 꺼줘", L(1, 5), lambda r, t, b: t["leds_on"] == []),
    ("LED 4번 깜빡이게 해줘", {}, lambda r, t, b: str(MODES.get("l1 led4", "")).startswith("pwm")),
    ("온도가 20도보다 높으면 LED 1번 켜줘", {}, lambda r, t, b: t["leds_on"] == [1]),
    ("온도가 30도 넘으면 LED 1번 켜고 아니면 LED 2번 켜줘", {}, lambda r, t, b: t["leds_on"] == [2]),
    ("자기장 x값이 양수면 LED 8번 켜줘", {"m1 mag-x": 9091}, lambda r, t, b: t["leds_on"] == [8]),
    ("온도가 25.5도 이상이면 LED 1번, 25.5도 미만이면 LED 2번 켜줘", {"t1 temperature": 25500}, lambda r, t, b: t["leds_on"] == [1]),
    ("LED 0번을 0.3초 간격으로 3번 깜빡여줘", {}, lambda r, t, b: not (r or {}).get("error") and (b.get("node_ms") or 0) >= 1000),
    ("1초마다 온도를 5번 재서 평균 알려줘", {}, lambda r, t, b: near(r, 23.75, 0.2) and (b.get("node_ms") or 0) >= 3500),
    ("LED 0번부터 15번까지 짝수 번호만 켜줘", {}, lambda r, t, b: t["leds_on"] == list(range(0, 16, 2))),
    ("LED 3번 끄고 7번 켜줘", L(3), lambda r, t, b: t["leds_on"] == [7]),
]

def effects_sig(b):
    fx = say.parse_effects(b.get("lines", [])) or {}
    devs = tuple(sorted((d["a"], tuple(d.get("regs", [])), d.get("r", 0) > 0) for d in fx.get("dev", [])))
    writes = tuple(sorted({(a, h) for _, wr, a, h, n, st in fx.get("x", []) if wr == "w" and st == 0 and len(h) > 2}))   # bytes that changed something (not pointer sets)
    return devs, writes

def manifest_sig(op):
    m = op.get("manifest") or {}
    return json.dumps(sorted((u.get("fn"), json.dumps(u.get("targets"), sort_keys=True), u.get("end")) for u in m.get("uses", [])), ensure_ascii=False)

def main():
    reps = int(sys.argv[sys.argv.index("--reps") + 1]) if "--reps" in sys.argv else 3
    lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else len(CASES)
    rows = []
    for s, bench, good in CASES[:lim]:
        for k in range(reps):
            record.RECORDS = tempfile.mkdtemp(prefix="e9-", dir=os.environ["POKE_RECORDS"])      # an empty memory for this one generation
            MODES.clear(); n0 = CALLS["n"]; t0 = time.time()
            row = {"sentence": s, "rep": k}
            try:
                op = say.make_op(s, log=lambda *a: None)
                row.update({"rounds": op.get("rounds"), "gen_s": op.get("gen_s"), "op_bytes": (op.get("check") or {}).get("op_bytes"),
                            "manifest": manifest_sig(op), "keys": None})
                b = say.boot(op["path"], {**say.default_bench(), **bench}, budget_s=25)
                r, t = b.get("result") or {}, b.get("truth") or {}
                row.update({"ok": bool(b["powered_off"]) and not r.get("error") and bool(good(r, t, b)), "result": r, "leds": t.get("leds_on"),
                            "node_ms": b.get("node_ms"), "keys": sorted(r.keys()), "effects": effects_sig(b),
                            "effects_ok": say.effects_report(b, op["path"]).get("ok")})
            except Exception as e:
                row.update({"ok": False, "error": f"{type(e).__name__}: {' '.join(str(e).split())[:160]}"})
            row["model_calls"] = CALLS["n"] - n0; row["secs"] = round(time.time() - t0, 1)
            rows.append(row)
            print(f"{'✓' if row['ok'] else '✗'} {s} #{k} rounds={row.get('rounds')} calls={row['model_calls']} {row['secs']}s "
                  f"result={json.dumps(row.get('result'), ensure_ascii=False)[:70]} leds={row.get('leds')} {row.get('error', '')}", flush=True)
    # per sentence: how alike are the three ops?
    per = []
    for s, _, _ in CASES[:lim]:
        rs = [r for r in rows if r["sentence"] == s]
        per.append({"sentence": s, "ok": sum(r["ok"] for r in rs), "n": len(rs),
                    "manifests": len({r.get("manifest") for r in rs}), "effect_devs": len({json.dumps(r.get("effects", [None])[0]) for r in rs}),
                    "effect_writes": len({json.dumps(r.get("effects", [None, None])[1]) for r in rs}),
                    "result_keysets": len({json.dumps(r.get("keys")) for r in rs}), "rounds": [r.get("rounds") for r in rs],
                    "op_bytes": [r.get("op_bytes") for r in rs], "gen_s": [r.get("gen_s") for r in rs]})
    for p in per:
        print(f"{p['sentence'][:34]:<36} ok {p['ok']}/{p['n']} · manifest {p['manifests']} · effects(dev/regs) {p['effect_devs']} · writes {p['effect_writes']} "
              f"· result keys {p['result_keysets']} · rounds {p['rounds']} · op B {p['op_bytes']}")
    tot = len(rows)
    print(f"\naccuracy {sum(r['ok'] for r in rows)}/{tot} · model calls {CALLS['n']} · round-1 {sum(1 for r in rows if r.get('rounds') == 1)}/{tot}")
    json.dump({"rows": rows, "per": per, "calls": CALLS["n"]}, open(os.path.join(HERE, "results", f"e9-{time.strftime('%Y%m%d-%H%M%S')}.json"), "w"),
              indent=1, ensure_ascii=False, default=list)

if __name__ == "__main__":
    main()
