"""E1 문장 모음 평가: 처음 듣는 문장을 빈 기록에서 hub.pipeline으로 실행하고 실험대 진실과 분류로 판정.
    cd sentence && ../sim/body/.venv/bin/python teams/test/e1_eval.py [--limit N]
본 기록을 건드리지 않게 POKE_RECORDS를 이 실행 전용 디렉터리로, 옛 op 캐시(build/ops)는 끔(POKE_LEGACY_OPS=0).
결과: teams/test/results/e1-<시각>.json (EVAL.md는 이 결과로 손으로 씀)."""
import os, sys, time, json, re, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
SENT = os.path.dirname(os.path.dirname(HERE))
STAMP = time.strftime("%Y%m%d-%H%M%S")
os.environ["POKE_RECORDS"] = os.path.join(HERE, "eval-records", STAMP)
os.environ["POKE_LEGACY_OPS"] = "0"
os.makedirs(os.environ["POKE_RECORDS"], exist_ok=True)
sys.path.insert(0, SENT); os.chdir(SENT)
import say, hub, claude_cli  # noqa: E402

CALLS = []
_ask = claude_cli.ask
def ask_counted(system, user, *a, **k):
    t = time.time()
    try:
        return _ask(system, user, *a, **k)
    finally:
        CALLS.append({"kind": "op" if system and "op for ONE sentence" in system else "other:" + (system or "")[:40].replace("\n", " "),
                      "s": round(time.time() - t, 1)})
claude_cli.ask = ask_counted

MODES = {}
_get = say.QMP.get
def get_rec(self, qid, prop):
    v = _get(self, qid, prop); MODES[f"{qid} {prop}"] = v; return v
say.QMP.get = get_rec
KST = datetime.timezone(datetime.timedelta(hours=9))

def nums(r):
    out = []
    def walk(x):
        if isinstance(x, bool): return
        if isinstance(x, (int, float)): out.append(x)
        elif isinstance(x, dict): [walk(v) for v in x.values()]
        elif isinstance(x, list): [walk(v) for v in x]
    walk(r); return out
def leds(t): return (t or {}).get("leds_on")
def near(r, v, tol): return any(abs(x - v) <= tol for x in nums(r))
def blob(r): return json.dumps(r, ensure_ascii=False)

# (sentence, kind, bench, expected class, oracle(result, truth, info) or None)
# class: read = boots without asking · write = asks confirm then boots · need / cannot / range / deny = no boot · long = scheduled boots
L = lambda *on: {f"l1 led{i}": "on" for i in on}
CASES = [
    ("방 온도 몇 도야?", "읽기", {}, "read", lambda r, t, i: near(r, 23.75, 0.2)),
    ("지금 온도를 소수점 둘째 자리까지 알려줘", "읽기", {}, "read", lambda r, t, i: near(r, 23.75, 0.07)),
    ("자기장 세기가 얼마나 돼?", "읽기", {"m1 mag-x": 27273, "m1 mag-y": 0}, "read", lambda r, t, i: near(r, 300, 10)),
    ("몇 시 몇 분이야", "읽기", {}, "read", lambda r, t, i: datetime.datetime.now(KST).hour in nums(r) or f"{datetime.datetime.now(KST).hour:02d}:" in blob(r)),
    ("오늘 날짜 알려줘", "읽기", {}, "read", lambda r, t, i: datetime.datetime.now(KST).day in nums(r) or datetime.datetime.now(KST).strftime("%m-%d") in blob(r)),
    ("LED 중에 켜져 있는 거 있어?", "읽기", L(4), "read", lambda r, t, i: (4 in nums(r) or "4" in blob(r)) and leds(t) == [4]),
    ("LED 6번 켜줘", "쓰기", {}, "write", lambda r, t, i: leds(t) == [6]),
    ("LED 2번이랑 9번 켜", "쓰기", {}, "write", lambda r, t, i: leds(t) == [2, 9]),
    ("LED 다 꺼줘", "쓰기", L(1, 5), "write", lambda r, t, i: leds(t) == []),
    ("LED 4번 깜빡이게 해줘", "쓰기", {}, "write", lambda r, t, i: str(MODES.get("l1 led4", "")).startswith("pwm")),
    ("온도가 20도보다 높으면 LED 1번 켜줘", "조건", {}, "write", lambda r, t, i: leds(t) == [1]),
    ("온도가 30도 넘으면 LED 1번 켜고 아니면 LED 2번 켜줘", "조건", {}, "write", lambda r, t, i: leds(t) == [2]),
    ("자기장 x값이 양수면 LED 8번 켜줘", "조건", {"m1 mag-x": 9091}, "write", lambda r, t, i: leds(t) == [8]),
    ("LED 0번을 0.3초 간격으로 3번 깜빡여줘", "반복", {}, "write", lambda r, t, i: not (r or {}).get("error") and (i.get("node_ms") or 0) >= 1000),
    ("1초마다 온도를 5번 재서 평균 알려줘", "반복", {}, "read", lambda r, t, i: near(r, 23.75, 0.2) and (i.get("node_ms") or 0) >= 3500),
    ("40초 동안 20초마다 온도 알려줘", "반복(긴)", {}, "long", lambda r, t, i: (r or {}).get("boots", 0) >= 3 and near(r, 23.75, 0.2)),
    ("그 LED 켜줘", "모호", {}, "need", None),
    ("적당히 밝게 해줘", "모호", {}, "need|cannot", None),
    ("음악 틀어줘", "불가능", {}, "cannot", None),
    ("습도 알려줘", "불가능", {}, "cannot", None),
    ("와이파이 연결해줘", "불가능", {}, "cannot", None),
    ("LED 16번 켜줘", "범위 밖", {}, "range|cannot", None),
    ("LED -1번 꺼줘", "범위 밖", {}, "range|cannot|need", None),
    ("시계를 오후 3시로 바꿔줘", "금지", {}, "deny", None),
    ("What's the temperature right now?", "영어", {}, "read", lambda r, t, i: near(r, 23.75, 0.2)),
    ("turn on LED 11 please", "영어", {}, "write", lambda r, t, i: leds(t) == [11]),
    ("LED 13 on 해줘", "혼용", {}, "write", lambda r, t, i: leds(t) == [13]),
    ("온도 ㄱㄱ", "구어체", {}, "read", lambda r, t, i: near(r, 23.75, 0.2)),
    ("엘이디 5번 좀 켜봐", "구어체", {}, "write", lambda r, t, i: leds(t) == [5]),
    ("온됴 알려줘", "오타", {}, "read", lambda r, t, i: near(r, 23.75, 0.2)),
    ("LED 3번 커줘", "오타", {}, "write|need", lambda r, t, i: leds(t) == [3]),
    ("나침반으로 지금 어느 방향 보고 있어?", "읽기", {"m1 mag-x": 9091, "m1 mag-y": 0}, "read", lambda r, t, i: not (r or {}).get("error")),
]

EXTRA = [   # harder: boundaries, sets, two-step writes, counting, waits
    ("온도가 25.5도 이상이면 LED 1번, 25.5도 미만이면 LED 2번 켜줘", "조건(경계)", {"t1 temperature": 25500}, "write", lambda r, t, i: leds(t) == [1]),
    ("LED 0번부터 15번까지 짝수 번호만 켜줘", "쓰기(집합)", {}, "write", lambda r, t, i: leds(t) == list(range(0, 16, 2))),
    ("LED 3번 끄고 7번 켜줘", "쓰기(두 단계)", L(3), "write", lambda r, t, i: leds(t) == [7]),
    ("온도 알려주고 25도 넘으면 경고해줘", "읽기+판단", {"t1 temperature": 26000}, "read", lambda r, t, i: near(r, 26.0, 0.1) and leds(t) == []),
    ("켜져 있는 LED 개수 세줘", "읽기(세기)", L(1, 2, 3), "read", lambda r, t, i: 3 in nums(r) and leds(t) == [1, 2, 3]),
    ("5초 기다렸다가 LED 10번 켜줘", "대기+쓰기", {}, "write", lambda r, t, i: leds(t) == [10] and (i.get("node_ms") or 0) >= 4900),
]
if "--extra" in sys.argv:
    CASES = EXTRA

def classify(evs):
    stop = next((e for e in evs if e.get("stage") == "stop"), None)
    asks = [e.get("kind") for e in evs if e.get("stage") == "ask"]
    boots = sum(1 for e in evs if e.get("stage") == "boot" and e.get("state") == "on")
    sched = any(e.get("stage") == "schedule" for e in evs)
    if sched: return "long"
    if stop and not boots: return stop.get("kind")
    if boots and "confirm" in asks: return "write"
    if boots: return "read"
    return stop.get("kind") if stop else "?"

def main():
    lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else len(CASES)
    rows = []
    for s, kind, bench, want, oracle in CASES[:lim]:
        MODES.clear(); n0 = len(CALLS); t0 = time.time(); evs = []
        asked = []
        def ask(k, q):
            asked.append(k)
            return "y" if k == "confirm" else None          # confirm yes; a question is left unanswered (counted as need)
        try:
            for ev in hub.pipeline(s, bench=bench, ask=ask):
                evs.append(ev)
        except Exception as e:
            evs.append({"stage": "stop", "kind": "exception", "text": repr(e)[:200]})
        for k in asked: evs.append({"stage": "ask", "kind": k})
        got = classify(evs)
        res = next((e for e in reversed(evs) if e.get("stage") == "result"), {})
        r, t = res.get("result"), res.get("truth") or {}
        bootev = [e for e in evs if e.get("stage") == "boot" and e.get("state") in ("off", "forced")]
        info = {"node_ms": bootev[-1].get("node_ms") if bootev else None}
        ready = [e for e in evs if e.get("stage") == "op" and e.get("state") == "ready"]
        cls_ok = got in want.split("|")
        acc = None
        if oracle and got in ("read", "write", "long"):
            try: acc = bool(oracle(r, t, info))
            except Exception: acc = False
        stop = next((e for e in evs if e.get("stage") == "stop"), {})
        row = {"sentence": s, "kind": kind, "want": want, "got": got, "class_ok": cls_ok, "accurate": acc,
               "rounds": ready[0].get("rounds") if ready else None, "cached": ready[0].get("cached") if ready else None,
               "retry": any(e.get("stage") == "op" and e.get("state") == "retry" for e in evs),
               "model_calls": len(CALLS) - n0, "call_kinds": [c["kind"][:12] for c in CALLS[n0:]], "secs": round(time.time() - t0, 1),
               "result": r, "leds_on": t.get("leds_on"), "led_modes": {k: v for k, v in MODES.items() if "led" in k and v != "off"},
               "stop": {"kind": stop.get("kind"), "text": (stop.get("text") or "")[:160]} if stop else None, "node_ms": info["node_ms"],
               "question": next((e.get("question") for e in evs if e.get("stage") == "op" and e.get("kind") == "need"), None)}
        rows.append(row)
        print(f"{'✓' if cls_ok and acc is not False else '✗'} [{kind}] {s} → {got} (want {want}) acc={acc} calls={row['model_calls']} "
              f"{row['secs']}s result={blob(r)[:80]} leds={row['leds_on']} stop={(row['stop'] or {}).get('text', '')[:60]}", flush=True)
    out = os.path.join(HERE, "results", f"e1-{STAMP}.json")
    json.dump({"stamp": STAMP, "records_dir": os.environ["POKE_RECORDS"], "rows": rows, "calls": CALLS}, open(out, "w"), indent=1, ensure_ascii=False)
    n = len(rows)
    print(f"\nclass {sum(r['class_ok'] for r in rows)}/{n}, accurate {sum(r['accurate'] is True for r in rows)}/{sum(r['accurate'] is not None for r in rows)}, "
          f"model calls {len(CALLS)}, → {out}")

if __name__ == "__main__":
    main()
