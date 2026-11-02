"""T11 지구력: 데모 서버 + 감시 2개(60 s) + 예약 부팅 + 넷부팅 트윈 + 기억된 문장 부하 + regress --quick 반복을 동시에.
모델 호출 0(POKE_CLAUDE 차단). 소유자 기록은 읽기만(isolate), 감시도 임시 POKE_WATCHES.
    cd sentence && ../sim/body/.venv/bin/python teams/test/e11_soak.py [minutes]   → results/e11-<시각>.jsonl (1분마다 한 줄) + 요약"""
import os, sys, time, json, glob, hashlib, tempfile, threading, subprocess, urllib.request, urllib.parse, traceback, random
HERE = os.path.dirname(os.path.abspath(__file__)); SENT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, SENT)
import isolate  # noqa: F401
os.environ["POKE_WATCHES"] = tempfile.mkdtemp(prefix="soak-watches-")
os.environ["POKE_CLAUDE"] = "/nonexistent/claude-blocked-by-soak"
os.chdir(SENT)
import say, hub  # noqa: E402

MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 60
STAMP = time.strftime("%Y%m%d-%H%M%S")
LOG = os.path.join(HERE, "results", f"e11-{STAMP}.jsonl")
STOP = threading.Event()
C = {}                                  # counters: name → {"ok", "bad", "err", "last_bad"}
LK = threading.Lock()
def count(name, ok, why=None):
    with LK:
        c = C.setdefault(name, {"ok": 0, "bad": 0, "last_bad": []})
        c["ok" if ok else "bad"] += 1
        if not ok and why: c["last_bad"] = (c["last_bad"] + [why[:160]])[-5:]
WIPES = {"n": 0, "nonzero": 0}
def wipe(b):
    w = b.get("wipe_check") or {}
    if w:
        with LK:
            WIPES["n"] += 1; WIPES["nonzero"] += int(w.get("nonzero", 0) > 0)

def nums(r): return [v for v in (r or {}).values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
MIX = [("온도 알려줘", {"t1 temperature": 23750}, lambda r, t: any(abs(v - 23.75) <= 0.1 for v in nums(r))),
       ("LED 3번 켜줘", {}, lambda r, t: t["leds_on"] == [3]),
       ("온도가 25도 넘으면 LED 0번을 켜고, 아니면 꺼줘", {"t1 temperature": 27250}, lambda r, t: t["leds_on"] == [0]),
       ("자기장 세기 알려줘", {"m1 mag-x": 27273, "m1 mag-y": 0}, lambda r, t: any(abs(v - 300) <= 10 for v in nums(r))),
       ("지금 몇 시야?", {}, lambda r, t: bool(r) and not r.get("error"))]

def load_worker():
    while not STOP.is_set():
        s, bench, good = random.choice(MIX)
        try:
            op = say.make_op(s, log=lambda *a: None)
            b = say.boot(op["path"], {**say.default_bench(), **bench}, budget_s=20); wipe(b)
            count("load", bool(b["powered_off"]) and good(b["result"], b["truth"]), f"{s}: {b['result']} {b['truth'].get('leds_on')}")
        except Exception as e:
            count("load", False, f"{type(e).__name__}: {e}")
        time.sleep(0.2)

# demo server
UPORT = 19500 + os.getpid() % 400
srv = subprocess.Popen([sys.executable, os.path.join(SENT, "ui", "server.py")], env={**os.environ, "POKE_UI_PORT": str(UPORT)}, cwd=SENT,
                       stdout=open(LOG + ".ui.log", "w"), stderr=subprocess.STDOUT)
def ui_say(sentence, answers=()):
    answers = list(answers); evs, rid = [], None
    with urllib.request.urlopen(f"http://127.0.0.1:{UPORT}/say?" + urllib.parse.urlencode({"sentence": sentence}), timeout=200) as r:
        for raw in r:
            l = raw.decode().strip()
            if not l.startswith("data: "): continue
            ev = json.loads(l[6:]); evs.append(ev)
            if ev.get("stage") == "run": rid = ev["run"]
            if ev.get("stage") == "ask":
                a = answers.pop(0) if answers else "n"
                urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{UPORT}/answer", data=json.dumps({"id": rid, "text": a}).encode(),
                                                              headers={"Content-Type": "application/json"}, method="POST"), timeout=10)
            if ev.get("stage") == "end": break
    return evs
def ui_worker():
    jobs = [("온도 알려줘", (), lambda res: any(abs(v - 23.75) <= 0.1 for v in nums(res.get("result")))),
            ("LED 5번 켜줘", ("y",), lambda res: 5 in ((res.get("truth") or {}).get("leds_on") or [])),
            ("LED 20번 켜줘", (), None), ("사진 찍어줘", (), None)]
    while not STOP.is_set():
        s, ans, good = random.choice(jobs)
        try:
            evs = ui_say(s, ans)
            res = next((e for e in reversed(evs) if e.get("stage") == "result"), {})
            stop = next((e for e in evs if e.get("stage") == "stop"), None)
            ok = (good(res) and not stop) if good else (stop is not None and not any(e.get("stage") == "boot" and e.get("state") == "on" for e in evs))
            empty = next((e for e in evs if e.get("stage") == "empty"), {})
            if good and not empty.get("zeroed"): ok = False
            count("ui", ok, f"{s}: stop={stop and stop.get('kind')} result={res.get('result')}")
        except Exception as e:
            count("ui", False, f"{type(e).__name__}: {e}")
        time.sleep(3)

def sched_worker():
    while not STOP.is_set():
        try:
            evs = list(hub.pipeline("30초 동안 10초마다 온도 알려줘", yes=True))
            res = next((e for e in reversed(evs) if e.get("stage") == "result"), {})
            r = res.get("result") or {}
            count("schedule", r.get("boots", 0) >= 4 and not any(e.get("stage") == "stop" for e in evs), f"result={str(r)[:120]}")
        except Exception as e:
            count("schedule", False, f"{type(e).__name__}: {e}")
        STOP.wait(120)

NPORT = 17500 + os.getpid() % 400
NB = os.path.join(SENT, "netboot")
def net_worker():
    import fcntl
    lock = open(os.path.join(say.BUILD, "regress-netboot.lock"), "w"); fcntl.flock(lock, fcntl.LOCK_EX)
    lg = open(LOG + ".netboot.log", "w")
    t = subprocess.Popen([sys.executable, "-u", os.path.join(NB, "tftpd.py"), "--port", str(NPORT)], stdout=lg, stderr=subprocess.STDOUT)
    v = subprocess.Popen([sys.executable, "-u", "vboot.py", "--port", str(NPORT)], cwd=NB, stdout=lg, stderr=subprocess.STDOUT)
    try:
        time.sleep(1.5)
        op = say.make_op("온도 알려줘", log=lambda *a: None)
        while not STOP.is_set():
            try:
                b = say.boot_netboot_twin(op["path"], budget_s=90)
                count("netboot", bool(b.get("powered_off")) and any(abs(x - 23.75) <= 0.1 for x in nums(b.get("result"))), f"{b.get('result')} {b.get('vm_status')}")
            except Exception as e:
                count("netboot", False, f"{type(e).__name__}: {e}")
            STOP.wait(20)
    finally:
        v.terminate(); t.terminate(); time.sleep(0.5); v.kill(); t.kill(); lock.close()

def regress_worker():
    while not STOP.is_set():
        r = subprocess.run([sys.executable, os.path.join(SENT, "regress.py"), "--quick"], capture_output=True, text=True, cwd=SENT,
                           env={k: v for k, v in os.environ.items() if k not in ("POKE_RECORDS", "POKE_WATCHES")})
        tail = [l for l in r.stdout.splitlines() if l.startswith("regress --quick")][-1:]
        fails = [l[:120] for l in r.stdout.splitlines() if l.startswith("✗ ") and "repro" not in l][:6]
        count("regress", r.returncode == 0, f"{tail} {fails}")
        with LK: C.setdefault("regress_fails", {"ok": 0, "bad": 0, "last_bad": []})["last_bad"] = fails

def arm_watches():
    import watch
    out = []
    chk = "온도를 읽고 30도를 넘었는지 알려줘"            # the product's recorded check op (reports hit) — no model
    for s, spec in [("온도가 30도 넘으면 알려줘 (soak A)", {"check": chk, "every_s": 60, "summary_ko": "온도 감시 A"}),
                    ("온도가 30도 넘으면 알려줘 (soak B)", {"check": chk, "every_s": 60, "summary_ko": "온도 감시 B"})]:
        d, prob = watch.prepare(s, spec)
        if prob: out.append(f"prepare failed: {prob}"); continue
        w, prob = watch.arm(d)
        out.append(w["id"] if w else f"arm failed: {prob}")
    return out
def watch_worker():
    import watch
    while not STOP.is_set():
        try:
            evs = watch.tick(notify=False, log=lambda *a: None)
            for e in evs or []:
                k = e.get("kind") if isinstance(e, dict) else str(e)
                count("watch_" + str(k), k not in ("stopped", "stop", "strike"), json.dumps(e, ensure_ascii=False, default=str)[:160])
        except Exception as e:
            count("watch_tick", False, f"{type(e).__name__}: {e}")
        STOP.wait(5)

def owner_digest():
    d = os.path.join(SENT, "build", "records")
    return hashlib.sha1("".join(f + hashlib.sha1(open(os.path.join(d, f), "rb").read()).hexdigest() for f in sorted(os.listdir(d)) if f.endswith(".json")).encode()).hexdigest()[:12]
def sample(t0):
    q = subprocess.run(["pgrep", "-f", "qemu-system-aarch64"], capture_output=True, text=True).stdout.split()
    du = subprocess.run(["du", "-sk", say.BUILD], capture_output=True, text=True).stdout.split()[0]
    locks = len(glob.glob(os.path.join(say.BUILD, "locks", "*"))) + len(glob.glob(os.path.join(os.environ["POKE_RECORDS"], ".locks", "*")))
    import watch
    ws = [{"id": w["id"], "state": w.get("state"), "count": w.get("count"), "bad": w.get("bad")} for w in watch.all_watches()]
    with LK:
        cs = {k: {"ok": v["ok"], "bad": v["bad"]} for k, v in C.items()}
    return {"t_min": round((time.time() - t0) / 60, 1), "counts": cs, "wipe": dict(WIPES), "qemu_all": len(q),
            "serial_files": len(glob.glob(os.path.join(say.BUILD, "serial", "*"))), "img_tmp": len(glob.glob(os.path.join(say.BUILD, "img", ".build-*"))),
            "fds": len(os.listdir("/dev/fd")), "threads": threading.active_count(), "locks": locks, "build_kb": int(du),
            "img_files": len(os.listdir(os.path.join(say.BUILD, "img"))), "owner": owner_digest(), "watches": ws}

def main():
    for s, _, _ in MIX: say.make_op(s, log=lambda *a: None)
    for _ in range(50):
        try: urllib.request.urlopen(f"http://127.0.0.1:{UPORT}/twin", timeout=1); break
        except OSError: time.sleep(0.1)
    armed = arm_watches()
    print("watches:", armed, flush=True)
    t0 = time.time(); first = sample(t0)
    ths = [threading.Thread(target=f, daemon=True) for f in [load_worker, load_worker, ui_worker, sched_worker, net_worker, regress_worker, watch_worker]]
    for th in ths: th.start()
    with open(LOG, "w") as lf:
        lf.write(json.dumps({"start": first, "armed": armed}, ensure_ascii=False) + "\n")
        while time.time() - t0 < MIN * 60:
            STOP.wait(60)
            s = sample(t0); lf.write(json.dumps(s, ensure_ascii=False) + "\n"); lf.flush()
            print(json.dumps({k: s[k] for k in ("t_min", "counts", "wipe", "qemu_all", "serial_files", "fds", "locks", "build_kb", "owner")}, ensure_ascii=False), flush=True)
    STOP.set()
    for th in ths: th.join(timeout=200)
    time.sleep(2)
    end = sample(t0)
    with LK:
        bad = {k: v["last_bad"] for k, v in C.items() if v["last_bad"]}
    summ = {"minutes": MIN, "start": first, "end": end, "last_bad": bad}
    open(LOG, "a").write(json.dumps({"end": summ}, ensure_ascii=False) + "\n")
    print("END", json.dumps(summ, ensure_ascii=False)[:3000])
    srv.kill(); srv.wait()

if __name__ == "__main__":
    try: main()
    finally:
        STOP.set()
        try: srv.kill()
        except Exception: pass
