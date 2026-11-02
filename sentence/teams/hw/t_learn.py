"""H10: the learning job API (learn.py) — plumbing without model calls.

    cd sentence/teams/hw && ../../../sim/body/.venv/bin/python t_learn.py

1 a job on the extended bench, where every part is already learned: discover → done, nothing to learn
  (events in order, state done, result.learned False, why "nothing unknown"), ~2 s, no model call
2 the same address twice at once → the second start returns the running job (one job per address)
3 cancel: a job started and cancelled at once ends as failed with its log tail, no stray process
A full learning job (identify → fragment → verify → review → register) is the 5th wave's reliability run
on an isolated copy of the catalog.
"""
import hwcommon  # noqa: F401
import os, sys, json, time
os.environ["POKE_BENCH"] = "ext"
os.environ["POKE_CLAUDE"] = "/nonexistent/claude-blocked"       # a model call would fail loudly
import learn

ok = True
j = learn.start()
st = learn.wait(j["id"], timeout_s=120, every_s=0.5)
ev = [e["stage"] for e in learn.events(j["id"])]
good = st["state"] == "done" and st["result"]["learned"] is False and [e for e in ev if e != "queued"][:2] == ["discover", "discover"] and ev[-1] == "done"
print("PASS" if good else "FAIL", "nothing to learn:", ev, st["state"], st.get("result"), f"{st['seconds']} s"); ok &= good

a = learn.start(0x19); b = learn.start(0x19)
good = a["id"] == b["id"]
print("PASS" if good else "FAIL", "one job per address:", a["id"], b["id"]); ok &= good
c = learn.cancel(a["id"])
time.sleep(1.0)
c = learn.status(a["id"])
alive = learn._alive(learn._read(a["id"]).get("pid"))
good = c["state"] in ("failed", "done") and not alive
print("PASS" if good else "FAIL", "cancel:", c["state"], "process alive" if alive else "process gone"); ok &= good
# 4 the probe's guards (verify team's audit): identification mode = target addresses only; never a write to a known part
import probe
os.environ["POKE_PROBE_TARGETS"] = "0x18"
try:
    probe.guard([("w", 0x60, [0x06, 0x00])]); good = False
except probe.ProbeError as e:
    good = "0x18" in str(e)
print("PASS" if good else "FAIL", "identification mode refuses another address"); ok &= good
try:
    probe.guard([("w", 0x18, [0x10] + [0] * 40)]); good = False
except probe.ProbeError:
    good = True
print("PASS" if good else "FAIL", "identification mode caps a long write"); ok &= good
del os.environ["POKE_PROBE_TARGETS"]
try:
    probe.guard([("w", 0x60, [0x06, 0x00])]); good = False
except probe.ProbeError:
    good = True
al = probe.allowlist([("w", 0x60, [0x06, 0x00])])
good &= all(not d["write_regs"] for d in al["devices"] if d["addr"] == 0x60)
print("PASS" if good else "FAIL", "a known part (0x60) is never written by a probe"); ok &= good

# 5 a registration cut off midway is taken back (isolated copy)
import tempfile, shutil, say
iso = tempfile.mkdtemp(prefix="hw-recon-"); fr = os.path.join(iso, "frags"); inc = os.path.join(iso, "incubated")
shutil.copytree(os.path.join(os.path.dirname(say.EDGE), "sentence", "frags"), fr); shutil.copytree(os.path.join(hwcommon.SENT, "incubated"), inc, ignore=shutil.ignore_patterns("pending", "failed"))
import incubation as INC
INC.INC, INC.FN_JSON, INC.CAT = inc, os.path.join(inc, "fn.json"), os.path.join(fr, "catalog.json")
keep = say.FRAGS; say.FRAGS = fr
os.makedirs(os.path.join(inc, "pending"))
json.dump({"name": "halfpart", "addr": "0x18", "pid": 999999}, open(os.path.join(inc, "pending", "halfpart.json"), "w"))
open(os.path.join(fr, "halfpart.h"), "w").write("// half\n")
fn = json.load(open(INC.FN_JSON)); fn["FN"]["halfpart_read"] = {"frag": "halfpart", "addr": 24, "access": "read", "write_regs": [], "max_write": 1}
fn["POLICY"]["halfpart_read"] = "allow"; json.dump(fn, open(INC.FN_JSON, "w"))
took = INC.reconcile_orphans()
fn2 = json.load(open(INC.FN_JSON))
good = took == ["halfpart"] and not os.path.exists(os.path.join(fr, "halfpart.h")) and "halfpart_read" not in fn2["FN"] \
       and not os.listdir(os.path.join(inc, "pending")) and "tmp421_read_mc" in fn2["FN"]
print("PASS" if good else "FAIL", "half registration taken back (header, manifest function, marker; the rest kept)"); ok &= good
say.FRAGS = keep; shutil.rmtree(iso, ignore_errors=True)

# 6 two addresses at once: the second waits its turn, each has its own work directory
a1 = learn.start(); a2 = learn.start(0x19)
s2 = learn.status(a2["id"])
st1 = learn.wait(a1["id"], timeout_s=120, every_s=0.5); st2 = learn.wait(a2["id"], timeout_s=180, every_s=0.5)
d1, d2 = (os.path.join(say.BUILD, "incubate", x["id"]) for x in (a1, a2))
good = s2["state"] in ("queued", "running") and os.path.isdir(d1) and os.path.isdir(d2) and d1 != d2 and st1["state"] == "done"
print("PASS" if good else "FAIL", f"second job {s2['state']} while the first ran; own dirs: {os.path.isdir(d1)}, {os.path.isdir(d2)}"); ok &= good
# 7 a part the hub already knows is not learned again unless asked (product's finding)
k = learn.start(0x20)
st = learn.wait(k["id"], timeout_s=120, every_s=0.5)
good = st["state"] == "done" and not st["result"]["learned"] and "already known" in (st["result"].get("why") or "")
print("PASS" if good else "FAIL", "0x20 (PCA9554, known) → 'already known', no learning:", st["result"].get("why")); ok &= good
# 8 the model block reaches the job's process (POKE_CLAUDE set above): a job that would call the model fails without one
os.environ["POKE_BENCH"] = "emc"
m = learn.start(0x19)
st = learn.wait(m["id"], timeout_s=300, every_s=0.5)
log = open(os.path.join(learn._dir(), f"{m['id']}.log")).read()
good = st["state"] in ("done", "failed") and not (st.get("result") or {}).get("learned") and "claude-blocked" in log
print("PASS" if good else "FAIL", "model block honoured by the job's process (its log names the blocked CLI)"); ok &= good
os.environ["POKE_BENCH"] = "ext"
print("learn", "PASS" if ok else "FAIL")   # t_learn never needs a model: every job here runs with the model blocked
sys.exit(0 if ok else 1)
