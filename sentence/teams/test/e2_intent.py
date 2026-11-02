"""E2 같은 뜻 매칭(제품 W1): 본 기록을 읽기만 하고(쓰지 않음) record.lookup → intent.match_rule → intent.match(모델)
순서로 어느 기록이 문장을 받는지 보고, 정답(기록의 첫 문장 + 파라미터)과 비교해 정밀도·재현율.
    cd sentence && ../sim/body/.venv/bin/python teams/test/e2_intent.py"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import isolate  # noqa: E402,F401 — 소유자 기록 보호(결정 7): POKE_RECORDS를 임시 사본으로
import os, sys, time, json
HERE = os.path.dirname(os.path.abspath(__file__))
SENT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, SENT); os.chdir(SENT)
import say, record, intent, claude_cli  # noqa: E402,F401

RECS = record.all_records()
def rid_of(first):
    return {r["id"] for r in RECS if r.get("sentences") and r["sentences"][0] == first}
T = "온도가 25도 넘으면 LED 0번을 켜고, 아니면 꺼줘"
F = ["온도를 화씨로만 알려줘", "온도를 화씨로 알려줘", "온도 화씨로 알려 줘"]
# (sentence, expected record first-sentence(s) or None, expected params subset or {})
CASES = [
    ("지금 몇 도야?", ["온도 알려줘"], {}),
    ("온도 좀 봐줘", ["온도 알려줘"], {}),
    ("기온 어때?", ["온도 알려줘"], {}),
    ("3번 LED 켜줘", ["LED 3번 켜줘"], {"P_LED": 3}),
    ("LED 8번 좀 켜 줄래?", ["LED 3번 켜줘"], {"P_LED": 8}),
    ("7번 엘이디 꺼줘", ["LED 9번 꺼줘"], {"P_LED": 7}),
    ("LED 3번 꺼줘", ["LED 9번 꺼줘"], {"P_LED": 3}),
    ("지금 시간 알려줘", ["지금 몇 시야?"], {}),
    ("자기장이 얼마나 세?", ["자기장 세기 알려줘"], {}),
    ("자기장 x축 값 알려줘", ["자기장 x축 값만 알려줘"], {}),
    ("온도를 화씨로 보여줘", F, {}),
    ("LED 4번 깜빡깜빡 해줘", ["LED 11번 깜빡여줘"], {"P_LED": 4}),
    ("온도가 28도를 넘으면 LED 2번을 켜고 아니면 꺼줘", [T], {"P_LED": 2}),
    ("LED 4번이랑 6번 켜줘", ["LED 1번이랑 2번 켜줘"], {"P_LED_A": 4, "P_LED_B": 6}),
    # must not be served by any record
    ("온도 30도 넘으면 LED 1번 켜줘", None, {}),            # no "else off": a different behaviour
    ("온도가 30도 넘으면 알려줘", None, {}),
    ("LED 3번 켜고 5초 뒤에 꺼줘", None, {}),
    ("1초마다 온도 5번 알려줘", None, {}),
    ("온도 평균 알려줘", None, {}),
    ("자기장 세기를 LED로 보여줘", None, {}),
    ("LED 3번 켜고 온도도 알려줘", None, {}),
    ("LED 전부 켜줘", None, {}),
    ("온도랑 시간 같이 알려줘", None, {}),
    ("LED 0번부터 3번까지 동시에 켜줘", None, {}),
    ("온도를 켈빈과 화씨로 알려줘", None, {}),
    ("지금 몇 시인지 LED로 보여줘", None, {}),
    # pass the guards (same device, structure, number count) — only the model can keep these apart
    ("자기장 y축만 알려줘", ["자기장 y축 값만 알려줘"], {}),
    ("자기장 z축 값만 알려줘", None, {}),
    ("자기장 세기를 가우스로 알려줘", None, {}),
    ("LED 3번 아주 잠깐만 켜줘", None, {}),
    ("온도를 화씨로 알려주고 끝에 경고음 내줘", None, {}),
]

def serve(s):
    calls = []
    def ask(sys_, p):
        t = time.time(); out = claude_cli.ask(sys_, p, timeout=60); calls.append(round(time.time() - t, 1)); return out
    hit = record.lookup(s)
    if hit:
        return ("lookup", hit.record if hit.kind in ("op", "range") else hit.record, hit.params if hit.kind == "op" else {}, hit.kind, calls)
    m = intent.match_rule(s, RECS)
    if m:
        return ("rule", m["record"], m["params"], "op", calls)
    if intent.candidates(s, RECS):
        m = intent.match(s, RECS, ask=ask)
        if m:
            return ("model", m["record"], m["params"], "op", calls)
        return ("model-none", None, {}, None, calls)
    return ("no-candidate", None, {}, None, calls)

def main():
    rows, tp, fp, fn, tn = [], 0, 0, 0, 0
    for s, want, wparams in CASES:
        how, rec, params, kind, calls = serve(s)
        got = rec["sentences"][0] if rec else None
        if want is None:
            ok = rec is None or kind == "range"
            tn += ok; fp += not ok
        else:
            ok = rec is not None and rec["sentences"][0] in want and all(params.get(k) == v for k, v in wparams.items())
            if ok: tp += 1
            elif rec is None: fn += 1
            else: fp += 1; fn += 1              # wrong record (or wrong number) counts against both
        rows.append({"sentence": s, "want": want, "got": got, "params": params, "how": how, "ok": ok, "model_s": calls})
        print(f"{'✓' if ok else '✗'} {s:<36} → {how:<12} {got!r} {params if params else ''} (want {want[0] if want else None!r})", flush=True)
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec_ = tp / (tp + fn) if tp + fn else 1.0
    calls = sum(len(r["model_s"]) for r in rows)
    print(f"\npositives {sum(1 for c in CASES if c[1])}: served right {tp}; negatives {sum(1 for c in CASES if not c[1])}: kept apart {tn}; "
          f"wrong serves {fp}; precision {prec:.2f}, recall {rec_:.2f}; model calls {calls}")
    json.dump({"rows": rows, "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": prec, "recall": rec_, "model_calls": calls},
              open(os.path.join(HERE, "results", f"e2-{time.strftime('%Y%m%d-%H%M%S')}.json"), "w"), indent=1, ensure_ascii=False)

if __name__ == "__main__":
    main()
