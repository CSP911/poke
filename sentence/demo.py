"""A run of sentences, each one boot, each checked against the bench's ground truth.
    .venv/bin/python demo.py            → build/demo.json"""
import json, os, time, datetime
import say

def rtc_ok(r, t):
    keys = [k for k in r if k != "error"]
    return bool(keys)

CASES = [
    ("온도 알려줘", {"t1 temperature": 23750},
     lambda r, t: any(isinstance(v, (int, float)) and abs(v - 23.75) <= 0.5 for v in r.values())),
    ("지금 몇 시야?", {}, rtc_ok),
    ("LED 3번 켜줘", {}, lambda r, t: t["leds_on"] == [3]),
    ("온도가 25도 넘으면 LED 0번을 켜고, 아니면 꺼줘", {"t1 temperature": 23750}, lambda r, t: t["leds_on"] == []),
    ("온도가 25도 넘으면 LED 0번을 켜고, 아니면 꺼줘", {"t1 temperature": 27250}, lambda r, t: t["leds_on"] == [0]),
    ("자기장 세기 알려줘", {"m1 mag-x": 27273, "m1 mag-y": 0}, lambda r, t: bool(r) and not r.get("error")),
    ("LED 0번부터 3번까지 0.2초 간격으로 차례로 켜줘", {}, lambda r, t: t["leds_on"] == [0, 1, 2, 3]),
]

def main():
    out = []
    for sentence, bench, good in CASES:
        r = say.run(sentence, bench=bench, yes=True)   # the demo answers its own confirmations
        res, truth = r["boot"]["result"] or {}, r["boot"]["truth"]
        try:
            ok = bool(r["boot"]["powered_off"]) and not res.get("error") and good(res, truth)
        except Exception:
            ok = False
        r["ok"] = ok
        print(f"   → {'✓' if ok else '✗'}")
        out.append(r)
    os.makedirs(say.BUILD, exist_ok=True)
    json.dump(out, open(os.path.join(say.BUILD, "demo.json"), "w"), indent=1, ensure_ascii=False)
    print(f"\n{sum(o['ok'] for o in out)}/{len(out)} sentences: correct and node powered off")

if __name__ == "__main__":
    main()
