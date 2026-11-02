"""E3-2 데모 서버 동시 요청: 읽기 8개 + 쓰기 4개(서로 다른 LED)를 한꺼번에. 모두 끝나는지, 결과가 맞는지,
트윈 LED 상태(/twin)가 쓰기 4개를 모두 담는지(진짜 칩이면 누적됨).
    cd sentence && ../sim/body/.venv/bin/python teams/test/e3_ui.py"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import isolate  # noqa: E402,F401 — 소유자 기록 보호(결정 7): POKE_RECORDS를 임시 사본으로
import os, sys, json, time, subprocess, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__)); SENT = os.path.dirname(os.path.dirname(HERE))
PORT = 19000 + os.getpid() % 500
env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}; env["POKE_UI_PORT"] = str(PORT); env["POKE_CLAUDE"] = "/nonexistent/no-model-in-e3"
srv = subprocess.Popen([sys.executable, os.path.join(SENT, "ui", "server.py")], env=env, cwd=SENT, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
U = f"http://127.0.0.1:{PORT}"
def post(path, body): return urllib.request.urlopen(urllib.request.Request(U + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST"), timeout=10).read()
def say(sentence, answers=()):
    answers = list(answers); evs, rid, t0 = [], None, time.time()
    with urllib.request.urlopen(U + "/say?" + urllib.parse.urlencode({"sentence": sentence}), timeout=120) as r:
        for raw in r:
            l = raw.decode().strip()
            if not l.startswith("data: "): continue
            ev = json.loads(l[6:]); evs.append(ev)
            if ev.get("stage") == "run": rid = ev["run"]
            if ev.get("stage") == "ask": post("/answer", {"id": rid, "text": answers.pop(0) if answers else "n"})
            if ev.get("stage") == "end": break
    res = next((e for e in reversed(evs) if e.get("stage") == "result"), {})
    stop = next((e for e in evs if e.get("stage") == "stop"), None)
    return {"sentence": sentence, "s": round(time.time() - t0, 2), "result": res.get("result"), "leds": (res.get("truth") or {}).get("leds_on"),
            "stop": stop and stop.get("kind"), "stop_text": stop and stop.get("text", "")[:80]}
try:
    for _ in range(50):
        try: urllib.request.urlopen(U + "/twin", timeout=1); break
        except OSError: time.sleep(0.1)
    post("/reset", {})
    jobs = [("온도 알려줘", ())] * 4 + [("지금 몇 시야?", ())] * 4 + [(f"LED {k}번 켜줘", ("y",)) for k in (8, 9, 10, 11)]
    t0 = time.time()
    with ThreadPoolExecutor(len(jobs)) as ex:
        rows = list(ex.map(lambda j: say(*j), jobs))
    wall = round(time.time() - t0, 2)
    twin = json.loads(urllib.request.urlopen(U + "/twin", timeout=5).read())
    for r in rows: print(r)
    ok_reads = sum(1 for r in rows[:8] if r["result"] and not r["stop"])
    ok_writes = sum(1 for r, k in zip(rows[8:], (8, 9, 10, 11)) if r["leds"] and k in r["leds"])
    print(f"\nwall {wall} s · reads ok {ok_reads}/8 · writes ok {ok_writes}/4 · /twin leds_on after all four writes: {twin.get('leds_on')} (a real chip would hold [8, 9, 10, 11])")
    json.dump({"wall_s": wall, "rows": rows, "twin_after": twin}, open(os.path.join(HERE, "results", f"e3-ui-{time.strftime('%Y%m%d-%H%M%S')}.json"), "w"), indent=1, ensure_ascii=False)
finally:
    srv.kill(); srv.wait()
