"""learn — "배워줘": a learning job the hub runs in the background (실기기팀 H10).

A person agrees to let the hub learn a part it has just met (bodycheck said "처음 보는 장치가 0x77에 있습니다").
The hub starts a job; the job runs sentence/incubation.py in its own process (minutes, a few model calls) and
writes one progress event per stage; the demo screen or the CLI follows the events; when it ends the result —
a part learned, or why not — is in the job record and in the hub's memory (incubated/<name>.json, or
incubated/failed/<addr>.json so the hub does not pretend next time).

    job = learn.start(0x77)                     # → {"id", "addr", "state": "running", …}; one job per address at a time
    learn.status(job["id"])                     # → {"state": running|done|failed, "stage", "events": n, "result", …}
    learn.events(job["id"], since=0)            # → [{"t", "stage", "state", …}, …]   (stages below)
    learn.jobs()                                # recent jobs, newest first
    learn.cancel(job["id"])

Stages (in order): discover → identify (start/done) → fragment (written, per round) → verify (pass/fail per round)
→ review (start/done — the verify team's safety review, say.incubation_review) → register (done) → done,
or failed (not verified: nothing registered). A job's files: build/learn/<id>.json (record) and
build/learn/<id>.events.jsonl. CLI: learn.py start ADDR | status ID | events ID | jobs | wait ID
"""
import os, sys, json, time, signal, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

def _dir():
    import say
    d = os.path.join(say.BUILD, "learn"); os.makedirs(d, exist_ok=True)
    return d

def _rec_path(jid): return os.path.join(_dir(), f"{jid}.json")
def _ev_path(jid): return os.path.join(_dir(), f"{jid}.events.jsonl")

def _alive(pid):
    if not pid:
        return False
    try:                                                     # our own child that ended: reap it (a zombie still "exists")
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except ChildProcessError:
        pass
    except OSError:
        return False
    try:
        os.kill(pid, 0); return True
    except (OSError, TypeError):
        return False

def _read(jid):
    try: return json.load(open(_rec_path(jid)))
    except (OSError, ValueError): return None

def jobs(limit=20):
    out = []
    for f in sorted(os.listdir(_dir()), reverse=True):
        if f.endswith(".json") and not f.endswith(".events.json"):
            r = _read(f[:-5])
            if r: out.append(status(r["id"]))
        if len(out) >= limit: break
    return out

def start(addr=None, bench_name=None, relearn=False):
    """Start learning the part at addr (None: every unknown part the scan finds). Returns the job record.
    A job already running for the same address is returned instead of starting a second one."""
    import say
    a = None if addr is None else (int(addr, 16) if isinstance(addr, str) else int(addr))
    with say.key_lock("learn-start"):
        live = [j for j in jobs(50) if j["state"] in ("running", "queued")]
        for j in live:
            if j.get("addr") == (f"0x{a:02x}" if a is not None else None):
                return j                                     # the same address: that job
        # another address while one runs: this one waits its turn (incubation.py holds the "learn-job" lock —
        # one bus, one bench, one learning at a time); it shows as "queued" until it starts
        queued_behind = [j["id"] for j in live if any(j["state"] == s_ for s_ in ("running", "queued")) and j.get("addr")]
        jid = time.strftime("%Y%m%d-%H%M%S") + f"-{os.urandom(2).hex()}"
        env = dict(os.environ)
        env["POKE_LEARN_EVENTS"] = _ev_path(jid)
        env["POKE_LEARN_JOB"] = jid                          # its own work directory (build/incubate/<id>)
        if bench_name: env["POKE_BENCH"] = bench_name
        cc = sys.modules.get("claude_cli")                    # a model block set in this process reaches the job
        if cc is not None and getattr(cc, "CLAUDE", "claude") != "claude":
            env["POKE_CLAUDE"] = cc.CLAUDE
        env.setdefault("POKE_BENCH", __import__("bench").name())
        log = open(os.path.join(_dir(), f"{jid}.log"), "w")
        cmd = [sys.executable, "-u", os.path.join(HERE, "incubation.py"), "run"] + ([f"0x{a:02x}"] if a is not None else []) + (["--relearn"] if relearn else [])
        p = subprocess.Popen(cmd, cwd=HERE, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        rec = {"id": jid, "addr": f"0x{a:02x}" if a is not None else None, "pid": p.pid, "started": time.time(),
               "started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "bench": env["POKE_BENCH"], "cmd": cmd,
               "queued_behind": queued_behind}
        open(_ev_path(jid), "a").close()
        say.write_json_atomic(_rec_path(jid), rec, indent=1)
        return status(jid)

def events(jid, since=0):
    try:
        lines = open(_ev_path(jid)).read().splitlines()
    except OSError:
        return []
    out = []
    for l in lines[since:]:
        try: out.append(json.loads(l))
        except ValueError: pass
    return out

STAGE_KO = {"queued": "차례 기다림", "discover": "버스 훑기", "identify": "식별", "fragment": "조각 쓰기", "verify": "트윈 검증",
            "review": "안전 심사", "register": "등록", "done": "끝", "failed": "실패"}

def status(jid):
    import say
    r = _read(jid)
    if not r:
        return {"id": jid, "state": "unknown"}
    ev = events(jid)
    last = ev[-1] if ev else {}
    done = next((e for e in reversed(ev) if e.get("stage") == "done"), None)
    failed = [e for e in ev if e.get("stage") == "failed"]
    if done:
        state = "done"                                       # finished; result.learned says whether a part was learned
    elif _alive(r.get("pid")):
        state = "queued" if (not ev or last.get("stage") == "queued") else "running"   # waiting for the job before it
    else:
        state = "failed"                                     # the process ended without a "done": crashed or cancelled
    res = None
    if done:
        res = {"learned": bool(done.get("registered")), "registered": done.get("registered"), "cost": done.get("cost"),
               "not_learned": [e.get("addr") for e in failed], "why": done.get("why")}
    out = {**{k: r.get(k) for k in ("id", "addr", "started_at", "bench")}, "state": state, "stage": last.get("stage"),
           "stage_ko": STAGE_KO.get(last.get("stage"), last.get("stage")), "events": len(ev),
           "seconds": round((done or last or {}).get("t", time.time()) - r["started"], 1) if ev else round(time.time() - r["started"], 1),
           "result": res}
    if state == "failed" and not done:
        try: out["log_tail"] = open(os.path.join(_dir(), f"{jid}.log")).read()[-800:]
        except OSError: pass
    if state != "running" and not r.get("ended"):
        r["ended"] = time.time(); r["state"] = state; r["result"] = res
        say.write_json_atomic(_rec_path(jid), r, indent=1)
    return out

def cancel(jid):
    r = _read(jid)
    if r and _alive(r.get("pid")):
        try: os.killpg(os.getpgid(r["pid"]), signal.SIGTERM)
        except OSError: pass
    return status(jid)

def wait(jid, timeout_s=3600, every_s=2, on_event=None):
    seen = 0
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        ev = events(jid, seen)
        for e in ev:
            if on_event: on_event(e)
        seen += len(ev)
        st = status(jid)
        if st["state"] not in ("running", "queued"):
            return st
        time.sleep(every_s)
    return status(jid)

if __name__ == "__main__":
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(2)
    if a[0] == "start":
        print(json.dumps(start(a[1] if len(a) > 1 else None), ensure_ascii=False, indent=1))
    elif a[0] == "status":
        print(json.dumps(status(a[1]), ensure_ascii=False, indent=1))
    elif a[0] == "events":
        for e in events(a[1]): print(json.dumps(e, ensure_ascii=False))
    elif a[0] == "jobs":
        for j in jobs(): print(json.dumps(j, ensure_ascii=False))
    elif a[0] == "wait":
        st = wait(a[1], on_event=lambda e: print(time.strftime("%H:%M:%S"), STAGE_KO.get(e.get("stage"), e.get("stage")), e.get("state", ""),
                                                   {k: v for k, v in e.items() if k in ("addr", "round", "name", "registered", "why")}, flush=True))
        print(json.dumps(st, ensure_ascii=False, indent=1))
    elif a[0] == "cancel":
        print(json.dumps(cancel(a[1]), ensure_ascii=False))
