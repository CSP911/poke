"""새 문장 6개: say.make_op(claude -p)로 op 생성 → tt- 태그로 다시 빌드 → 부팅 → 실험대 진실과 대조.
    ../../../sim/body/.venv/bin/python t_sentences.py
(make_op 자신은 캐시 키를 태그로 빌드한다 — 바꿀 수 없음.)"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import isolate  # noqa: E402,F401 — 소유자 기록 보호(결정 7): POKE_RECORDS를 임시 사본으로
import math, time
from common import say, build_tt, save

seen = {}
_orig_get = say.QMP.get
def _get(self, qid, prop):
    v = _orig_get(self, qid, prop); seen[f"{qid} {prop}"] = v; return v
say.QMP.get = _get                                  # 이 프로세스 안에서만: LED 모드(on/off/pwm)를 기록

def nums(r):
    return [v for k, v in (r or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)]

CASES = [
    ("Turn on LED 7 and tell me the temperature in Fahrenheit", {"t1 temperature": 25000},
     lambda r, t, b: t["leds_on"] == [7] and any(abs(v - 77.0) <= 1.0 for v in nums(r)), "LED 7 켜짐 + 77°F(±1)"),
    ("나침반 방향 알려줘", {"m1 mag-x": 9091, "m1 mag-y": 9091},
     lambda r, t, b: any(abs(v - 45) <= 3 or abs(v - 315) <= 3 for v in nums(r)), "x=y=100 → 45° 또는 315°(관례 차)"),
    ("불 좀 켜줘", {}, lambda r, t, b: bool(t["leds_on"]) and not (r or {}).get("error"), "모호: LED 하나 이상 켜지면 통과(어떤 걸 켰는지 기록)"),
    ("temperature가 30도 이상이면 LED 1 blink 시켜, 아니면 LED 1 꺼줘", {"t1 temperature": 31000},
     lambda r, t, b: str(seen.get("l1 led1", "")).startswith("pwm") or "blink" in str(seen.get("l1 led1", "")), "31°C → LED1 깜빡임(pwm)"),
    ("LED 2번 켜고 12초 뒤에 꺼줘", {},
     lambda r, t, b: t["leds_on"] == [] and (b["node_ms"] or 0) >= 11500, "끝에 꺼짐 + 노드 시간 ≥ 12 s (delay_ms 10 s 상한)"),
    ("LED 20번 켜줘", {}, lambda r, t, b: bool((r or {}).get("error")) and t["leds_on"] == [], "없는 LED → 이유와 함께 실패, 아무것도 안 켬"),
]
out = []
for i, (sentence, bench, good, oracle) in enumerate(CASES):
    row = {"sentence": sentence, "oracle": oracle}
    t0 = time.time()
    try:
        op = say.make_op(sentence, log=lambda *a: print(*a))
    except Exception as e:
        row["gen_error"] = repr(e)[:300]; out.append(row); print("✗ gen", sentence, e); continue
    row.update({"key": op["key"], "cached": op["cached"], "gen_s": op["gen_s"], "rounds": op["rounds"], "op_bytes": op["check"]["op_bytes"], "src": op["src"]})
    _, _, path, chk = build_tt(op["src"], f"tt-sent-{i}")
    seen.clear()
    try:
        b = say.boot(path, bench, budget_s=30)
        ok = bool(b["powered_off"]) and bool(good(b["result"], b["truth"], b))
        row.update({"ok": ok, "result": b["result"], "truth": b["truth"], "leds_modes": {k: v for k, v in seen.items() if v != "off"},
                    "off": b["powered_off"], "node_ms": b["node_ms"], "say": [l for l in b["lines"] if l.startswith(("POKE-SAY", "POKE-FAULT"))]})
    except Exception as e:
        row.update({"ok": False, "hub_exception": repr(e)[:200]})
    row["total_s"] = round(time.time() - t0, 1)
    out.append(row)
    print(f"{'✓' if row.get('ok') else '✗'} [{i}] {sentence} | gen {row['gen_s']}s r{row['rounds']} | result {row.get('result')} | "
          f"leds {row.get('leds_modes')} | node_ms {row.get('node_ms')} | {row.get('say')} {row.get('hub_exception', '')}")
save("sentences", out)
print(f"\n{sum(bool(r.get('ok')) for r in out)}/{len(out)} correct")
