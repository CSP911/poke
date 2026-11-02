"""X2: the FAULT retry asks before it repeats a change to the room (hub.pipeline + say.retry_safe).
Runs the pipeline with make_op patched (no model): first op faults, the second is a good one.
    ../sim/body/.venv/bin/python teams/verify/t_x2.py"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import tempfile; os.environ["POKE_RECORDS"] = tempfile.mkdtemp(prefix="verify-records-")   # never the owner's records
import say, hub, json, time
T = os.path.join(HERE, "edge", "tests")

def run(first, answer):
    ops = [say._op_dict(open(os.path.join(T, first + ".c")).read(), "vt-x2-" + first, cached=True, gen_s=0, rounds=0),
           say._op_dict(open(os.path.join(T, "ok_hello.c")).read(), "vt-x2-ok", cached=True, gen_s=0, rounds=0)]
    calls, asked, boots = [], [], []
    def mk(sentence, log=None, feedback=None, **kw):
        calls.append(feedback); return ops[min(len(calls) - 1, 1)]
    orig_mk, orig_boot = say.make_op, say.boot
    say.make_op = mk
    # count only this test's images: the pipeline's body check may boot scan images in between (bodywatch hint)
    say.boot = lambda *a, **k: (boots.append(1) if "vt-x2-" in str(a[0] if a else k.get("img_path", "")) else None, orig_boot(*a, **k))[1]
    try:
        evs = list(hub.pipeline("x2 test", ask=(lambda kind, q: (asked.append(q), answer)[1]) if answer is not None else None, yes=True))
    finally:
        say.make_op, say.boot = orig_mk, orig_boot
    run.last = [e for e in evs if e.get("stage") in ("boot", "op", "confirm", "stop", "result")]
    return len(boots), asked

cases = [
    ("read-only fault → retried without asking", "bad_null_read", None, lambda n, q: n == 2 and not q),
    ("LED changed, then fault, answer n → no second boot", "x2_write_then_fault", "n", lambda n, q: n == 1 and q and "LED" in q[0]),
    ("LED changed, then fault, answer y → retried", "x2_write_then_fault", "y", lambda n, q: n == 2 and q),
    ("LED changed, then fault, no one to ask → stop", "x2_write_then_fault", None, lambda n, q: n == 1 and not q),
]
good = 0
for name, first, ans, ok in cases:
    n, q = run(first, ans)
    r = bool(ok(n, q)); good += r
    print(f"{'✓' if r else '✗'} {name:<52} boots={n} asked={q[:1]}")
    if not r:                                         # under load: show what the pipeline saw (no record? boot cut?)
        detail = [{k: v for k, v in e.items() if k in ("stage", "state", "why", "fault", "node_ms", "problem", "summary", "text")} for e in run.last]
        for e in detail: print("      ", e)
        with open(os.path.join(say.BUILD, "t_x2-failures.jsonl"), "a") as f:   # regress keeps only our last line
            f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "case": name, "boots": n, "asked": q, "events": detail}, ensure_ascii=False, default=str) + "\n")
print(f"\n{good}/{len(cases)} retry-safety cases as expected")
sys.exit(0 if good == len(cases) else 1)
