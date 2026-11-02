"""Theseus experiment: does a body-free *memory record* (sentences, body
fingerprint, outcome trace, failure lineage, tuned numbers — no C source, no
binary) let a skill reach a given level on a never-seen body in fewer trials
than starting from a blank page?

  A = pppp (origin body)   B = ppppp, C = pypy (unseen bodies)
  arms per unseen body:  T = generate once with the memory as library text
                         X = generate once from blank (control)
  then tune (pop 48 x 6 gens); compare the best-forward curve per generation.

Usage: .venv/bin/python theseus.py prep|origin N|memory|arms X|T|summary|twin
Writes build/theseus/result.json and logs."""
import json, os, re, subprocess, sys, time, math, ctypes
import numpy as np
import sim, tune, discover

OUT = os.path.join(sim.BUILD, "theseus")
SK = os.path.join(sim.HERE, "skills")
INTENT = "crawl toward the light, which is straight ahead of the head (+x), as fast as you can"
NOTES = ("Lies on flat ground with ordinary friction, head first. The head is at the front (+x); "
         "moving forward means the whole body travels toward +x.")
POP, GENS, TSEC = 48, 6, 6.0
REPS = int(os.environ.get("THESEUS_REPS", "3"))
BODIES = {"B": "ppppp", "C": "pypy"}

def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    with open(os.path.join(OUT, "log.txt"), "a") as f: f.write(s + "\n")

SYSTEM_TMPL = None
def system_prompt():
    abi = open(os.path.join(sim.HERE, "..", "..", "edge", "kernel", "pi4", "motion_io.h")).read()
    js = open(os.path.join(sim.HERE, "generate.js")).read()
    body = js[js.index("const system = `") + len("const system = `"):]
    body = body[:body.index("`\n\nconst user")]
    return body.replace("${abi}", abi).replace("${100}", "100")

def write_prompt(body, tag, library=None, feedback=None):
    """Exactly the prompt generate.js would send (system + user), written to a file;
    a blind generator (a fresh model instance with no other context) answers it."""
    b = dict(body); b["notes"] = NOTES
    if library: b["library"] = library
    user = ("Body (discovered by the robot itself by wiggling each joint):\n" + json.dumps(b, indent=2)
            + f"\n\nIntent: {INTENT}\n" + (f"\nPrevious attempt result (improve on it): {feedback}" if feedback else ""))
    p = os.path.join(OUT, f"prompt-{tag}.txt")
    with open(p, "w") as f:
        f.write("=== SYSTEM ===\n" + system_prompt() + "\n\n=== USER ===\n" + user + "\n")
    return p

def read_meta(tag):
    src = os.path.join(SK, f"gen-theseus-{tag}.c")
    if not os.path.exists(src): return src, None, "no file generated"
    code = open(src).read().strip()
    code = re.sub(r"^```\w*\n?", "", code); code = re.sub(r"\n?```$", "", code)
    open(src, "w").write(code + "\n")
    m = re.match(r".*SKILL-META\s+(\{.*\})", code.split("\n")[0])
    if not m: return src, None, "missing SKILL-META line"
    return src, json.loads(m.group(1)), None

def sat_pct(res, seconds, n):
    ev = res["safety"]; return (ev["rate_limited"] + ev["clamped"]) / (seconds * sim.HZ * n) * 100

def evaluate(axes, src, meta, tag):
    """compile → generated-as-is runs → tune (curve) → final 10 s run."""
    try:
        lib = sim.compile_skill(src, f"gen-theseus-{tag}")
    except RuntimeError as e:
        return {"ok": False, "error": "compile: " + str(e).splitlines()[0][:200]}
    ranges = [(float(p["lo"]), float(p["hi"])) for p in meta["params"]]
    mid = sim.run(axes, lib, params=tuple((lo + hi) / 2 for lo, hi in ranges), seconds=10)
    dflt = sim.run(axes, lib, params=tuple(0.0 for _ in ranges), seconds=10)   # the skill's own defaults
    curve = []
    def tlog(s):
        m = re.search(r"best ([+-][\d.]+) cm", s)
        if m: curve.append(float(m.group(1)))
    t0 = time.time()
    best, best_s, hist = tune.tune(axes, lib, ranges, seconds=TSEC, pop=POP, gens=GENS, log=tlog)
    tt = time.time() - t0
    res = sim.run(axes, lib, params=tuple(best), seconds=10, record=True)
    tr = res["trace"]; pts = [tr[min(i, len(tr) - 1)] for i in (0, 50, 100, 150, 199)]
    return {"ok": True, "lib": lib, "name": meta["name"], "idea": meta.get("idea", ""),
            "param_names": [p["name"] for p in meta["params"]], "ranges": ranges,
            "best": [round(float(v), 4) for v in best],
            "mid_cm": round(mid["forward"] * 100, 2), "default_cm": round(dflt["forward"] * 100, 2),
            "curve6s_cm": curve, "score_hist": [round(h, 4) for h in hist],
            "final_cm": round(res["forward"] * 100, 2), "lateral_cm": round(res["lateral"] * 100, 2),
            "safety_pct": round(sat_pct(res, 10, len(axes)), 2), "estop": res["safety"]["estop"],
            "trace_xy_cm": [[round(p[0] * 100, 1), round(p[1] * 100, 1)] for p in pts],
            "tune_s": round(tt, 1)}

def memory_record(body, rounds, chosen):
    """What travels to the next body. No C source, no binary."""
    c = chosen
    rec = {
        "kind": "theseus memory record (who this robot is, for this intent) — not code",
        "intent": INTENT,
        "origin_body_fingerprint": {"summary": body["summary"],
                                    "joints": [{k: j[k] for k in ("joint", "axis", "head_pitch_deg", "head_yaw_deg")}
                                               for j in body["joints"]]},
        "skill": {"name": c["name"], "idea": c["idea"],
                  "params": [{"name": n, "lo": r[0], "hi": r[1], "tuned": v}
                             for n, r, v in zip(c["param_names"], c["ranges"], c["best"])]},
        "outcome_trace": {"forward_cm_10s": c["final_cm"], "lateral_cm_10s": c["lateral_cm"],
                          "safety_intervention_pct": c["safety_pct"],
                          "com_xy_cm_at_0_2.5_5_7.5_10s": c["trace_xy_cm"],
                          "untuned_midrange_forward_cm": c["mid_cm"]},
        "failure_lineage": [f"round {r['round']}: '{r.get('name','?')}' — {r.get('idea', r.get('error',''))} → "
                            + (f"{r['final_cm']:+.1f} cm/10s after tuning, safety {r['safety_pct']:.1f}%"
                               if r.get("ok") else r.get("error", "failed"))
                            for r in rounds if r is not c],
        "advice": "This came from a different body (shape above). Your body may differ: keep what transfers "
                  "(the gait idea, direction signs, tuned magnitudes) and adapt to your own joints.",
    }
    return rec

def J(name, obj=None):
    p = os.path.join(OUT, name)
    if obj is None: return json.load(open(p))
    with open(p, "w") as f: json.dump(obj, f, indent=1, ensure_ascii=False)

def stage_prep():
    """discover every body; write the prompts for origin round 1 and the blank-page arms."""
    os.makedirs(OUT, exist_ok=True); open(os.path.join(OUT, "log.txt"), "w").close()
    bodies = {}
    for k, ax in [("A", "pppp")] + list(BODIES.items()):
        bodies[k] = discover.discover(list(ax), log=lambda s: None)
        found = "".join("p" if x["axis"] == "pitch" else "y" for x in bodies[k]["joints"])
        log(f"[discover] {k} {ax} → found {found}: {bodies[k]['summary']}")
    J("bodies.json", bodies)
    write_prompt(bodies["A"], "A1")
    for k in BODIES:
        for r in range(REPS): write_prompt(bodies[k], f"X{k}{r}")

def stage_origin(rnd):
    bodies = J("bodies.json")
    src, meta, err = read_meta(f"A{rnd}")
    r = {"round": rnd, "ok": False, "error": err} if meta is None else evaluate(list("pppp"), src, meta, f"A{rnd}")
    r["round"] = rnd
    if meta: r.setdefault("name", meta["name"]); r.setdefault("idea", meta.get("idea", ""))
    r.pop("lib", None); J(f"origin-A{rnd}.json", r)
    if not r["ok"]:
        log(f"[A{rnd}] {r['error']}"); fb = f"It did not compile: {r['error']}"
    else:
        log(f"[A{rnd}] '{r['name']}': {r['idea']}\n      mid {r['mid_cm']:+.1f} dflt {r['default_cm']:+.1f} curve {r['curve6s_cm']} → tuned {r['final_cm']:+.1f} cm/10s, safety {r['safety_pct']}%")
        fb = (f"Your skill '{r['name']}' ({r['idea']}) after tuning its params on the body "
              f"moved {r['final_cm']:+.1f} cm forward in 10 s (sideways {r['lateral_cm']:+.1f} cm). "
              f"Best params: {dict(zip(r['param_names'], r['best']))}. "
              f"Safety layer intervened on {r['safety_pct']:.1f}% of joint-ticks (keep this low). "
              f"Rethink the movement STRUCTURE to travel farther toward +x; do not just change numbers.")
    if rnd == 1: write_prompt(bodies["A"], "A2", feedback=fb)

def stage_memory():
    bodies = J("bodies.json")
    rounds = [J(f"origin-A{r}.json") for r in (1, 2) if os.path.exists(os.path.join(OUT, f"origin-A{r}.json"))]
    good = [r for r in rounds if r.get("ok")]
    chosen = max(good, key=lambda r: r["final_cm"])
    rec = memory_record(bodies["A"], rounds, chosen)
    text = json.dumps(rec, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "memory.json"), "w") as f: f.write(text)
    log(f"[memory] chosen A{chosen['round']} '{chosen['name']}' {chosen['final_cm']:+.1f} cm; record {len(text.encode())} B (source 0 B, binary 0 B)")
    for k in BODIES:
        for r in range(REPS): write_prompt(bodies[k], f"T{k}{r}", library=[{"text": text}])

def stage_arms(which):
    for arm in which:
        for k in BODIES:
            for r in range(REPS):
                tag = f"{arm}{k}{r}"
                src, meta, err = read_meta(tag)
                row = {"ok": False, "error": err} if meta is None else evaluate(list(BODIES[k]), src, meta, tag)
                if meta: row.setdefault("name", meta["name"]); row.setdefault("idea", meta.get("idea", ""))
                row.update({"arm": arm, "body": k, "axes": BODIES[k], "rep": r}); row.pop("lib", None)
                J(f"arm-{tag}.json", row)
                log(f"[{tag}] " + (f"'{row['name']}' mid {row['mid_cm']:+.1f} dflt {row['default_cm']:+.1f} "
                    f"curve {row['curve6s_cm']} final {row['final_cm']:+.1f} safety {row['safety_pct']}%\n      idea: {row['idea']}"
                    if row["ok"] else row["error"]))

def stage_summary():
    arms = [J(f"arm-{a}{k}{r}.json") for a in "TX" for k in BODIES for r in range(REPS)
            if os.path.exists(os.path.join(OUT, f"arm-{a}{k}{r}.json"))]
    rounds = [J(f"origin-A{r}.json") for r in (1, 2) if os.path.exists(os.path.join(OUT, f"origin-A{r}.json"))]
    mem = open(os.path.join(OUT, "memory.json")).read()
    summary = {}
    for k in BODIES:
        okr = [a for a in arms if a["body"] == k and a["ok"]]
        top = max(max(a["curve6s_cm"]) for a in okr); thr = 0.7 * top
        for a in arms:
            if a["body"] != k: continue
            hit = next((g for g, v in enumerate(a["curve6s_cm"]) if v >= thr), None) if a["ok"] else None
            a["trials_to_thr"] = None if hit is None else (hit + 1) * POP
        summary[k] = {"threshold_6s_cm": round(thr, 2), "top_6s_cm": top}
        for arm in ("T", "X"):
            rows = [a for a in arms if a["body"] == k and a["arm"] == arm]
            tr = [a["trials_to_thr"] if a["trials_to_thr"] is not None else math.inf for a in rows]
            summary[k][arm] = {"trials": [None if math.isinf(t) else t for t in tr],
                               "median_trials": (None if math.isinf(float(np.median(tr))) else float(np.median(tr))) if tr else None,
                               "reached": sum(a["trials_to_thr"] is not None for a in rows), "n": len(rows),
                               "median_mid_cm": float(np.median([a["mid_cm"] for a in rows if a["ok"]] or [np.nan])),
                               "median_default_cm": float(np.median([a["default_cm"] for a in rows if a["ok"]] or [np.nan])),
                               "median_final_cm": float(np.median([a["final_cm"] for a in rows if a["ok"]] or [np.nan]))}
        log(f"[summary {k}] {json.dumps(summary[k])}")
    res = {"intent": INTENT, "pop": POP, "gens": GENS, "tune_seconds": TSEC, "reps": REPS,
           "generator": "blind fresh model instances answering generate.js's exact prompt (API key had no credit)",
           "memory_bytes": len(mem.encode()), "binary_bytes_transferred": 0, "source_bytes_transferred": 0,
           "origin_rounds": rounds, "arms": arms, "summary": summary}
    if os.path.exists(os.path.join(OUT, "twin.json")): res["twin"] = J("twin.json")
    if os.path.exists(os.path.join(OUT, "seeds.json")): res["tuner_seeds"] = J("seeds.json")
    J("result.json", res)
    print(f"\n{'body':<5}{'arm':<4}{'rep':<4}{'skill':<30}{'mid':>8}{'dflt':>8}{'to-thr':>8}{'final':>8}{'safety%':>9}")
    for a in sorted(arms, key=lambda a: (a["body"], a["arm"], a["rep"])):
        if a["ok"]:
            print(f"{a['body']:<5}{a['arm']:<4}{a['rep']:<4}{a['name'][:29]:<30}{a['mid_cm']:>8.1f}{a['default_cm']:>8.1f}"
                  f"{str(a['trials_to_thr'] or '미도달'):>8}{a['final_cm']:>8.1f}{a['safety_pct']:>9.1f}")
        else:
            print(f"{a['body']:<5}{a['arm']:<4}{a['rep']:<4}FAILED {a['error']}")

def stage_seeds(nseeds=5):
    """Separate tuner luck from generator luck: re-tune every arm with seeds 0..n-1,
    and run each skill's own defaults (all params 0) for 6 s."""
    out = {}
    for a in "TX":
        for k in BODIES:
            for r in range(REPS):
                tag = f"{a}{k}{r}"; lib = os.path.join(sim.BUILD, f"gen-theseus-{tag}.dylib")
                row = J(f"arm-{tag}.json")
                if not row["ok"]: continue
                curves = []
                for sd in range(nseeds):
                    c = []
                    def tlog(s, c=c):
                        m = re.search(r"best ([+-][\d.]+) cm", s)
                        if m: c.append(float(m.group(1)))
                    tune.tune(list(BODIES[k]), lib, [tuple(x) for x in row["ranges"]], seconds=TSEC, pop=POP, gens=GENS, seed=sd, log=tlog)
                    curves.append(c)
                d6 = sim.run(list(BODIES[k]), lib, params=tuple(0.0 for _ in row["ranges"]), seconds=TSEC)
                out[tag] = {"curves": curves, "default_6s_cm": round(d6["forward"] * 100, 2)}
                log(f"[seeds {tag}] finals {[c[-1] for c in curves]} default6s {out[tag]['default_6s_cm']}")
    J("seeds.json", out)
    for k in BODIES:
        top = max(max(max(c) for c in v["curves"]) for t, v in out.items() if t[1] == k); thr = 0.7 * top
        line = f"[seeds summary {k}] thr6s {thr:.1f} cm (70% of {top:.1f})"
        for a in "TX":
            tr = []
            for t, v in out.items():
                if t[0] != a or t[1] != k: continue
                for c in v["curves"]:
                    hit = next((g for g, x in enumerate(c) if x >= thr), None)
                    tr.append(math.inf if hit is None else (hit + 1) * POP)
            reached = sum(not math.isinf(x) for x in tr)
            line += f" | {a}: median trials {np.median(tr)} reached {reached}/{len(tr)} all {tr}"
        log(line)

def stage_twin():
    arms = [J(f"arm-T{k}{r}.json") for k in BODIES for r in range(REPS)
            if os.path.exists(os.path.join(OUT, f"arm-T{k}{r}.json"))]
    best = max([a for a in arms if a["ok"]], key=lambda a: a["final_cm"])
    best["lib"] = os.path.join(sim.BUILD, f"gen-theseus-T{best['body']}{best['rep']}.dylib")
    try:
        t = twin_check(best)
    except Exception as e:
        t = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    J("twin.json", t); log(f"[twin] {t}")

def twin_check(a, seconds=10.0):
    import mujoco, pokeedge
    axes = list(a["axes"]); n = len(axes)
    src = os.path.join(SK, f"gen-theseus-{a['arm']}{a['body']}{a['rep']}.c")
    img = pokeedge.build(src, f"theseus-{a['arm']}{a['body']}{a['rep']}")
    local = sim.run(axes, a["lib"], params=tuple(a["best"]), seconds=seconds)
    with pokeedge.Twin() as host:
        edge = pokeedge.PokeSkill(host)
        edge.load(img, [1.2] * n, [0 if x == "p" else 1 for x in axes])
        edge.attempt(a["best"])
        m = mujoco.MjModel.from_xml_string(sim.body_xml(axes)); d = mujoco.MjData(m)
        sub = int(round(1.0 / sim.HZ / m.opt.timestep)); mujoco.mj_forward(m, d)
        start = d.subtree_com[1].copy(); lat = []; ev = {}
        for k in range(int(seconds * sim.HZ)):
            w, x, y, z = d.sensordata[0:4]; r, p, yw = sim.quat_to_rpy(w, x, y, z)
            imu = (r, p, yw, *[float(v) for v in d.sensordata[4:7]])
            t0 = time.time()
            cmd, ev = edge.step(k / sim.HZ, k, [float(d.qpos[7 + j]) for j in range(n)], imu)
            lat.append((time.time() - t0) * 1000)
            d.ctrl[:n] = cmd
            for _ in range(sub): mujoco.mj_step(m, d)
        edge.stop()
        dd = d.subtree_com[1] - start
    return {"ok": True, "skill": f"{a['arm']}{a['body']}{a['rep']} {a['name']}", "image_bytes": len(img),
            "twin_forward_cm": round(float(dd[0]) * 100, 2), "twin_lateral_cm": round(float(dd[1]) * 100, 2),
            "local_forward_cm": round(local["forward"] * 100, 2), "local_lateral_cm": round(local["lateral"] * 100, 2),
            "diff_cm": round(abs(float(dd[0]) - local["forward"]) * 100, 3),
            "twin_safety": ev, "local_safety": local["safety"],
            "rtt_ms_median": round(float(np.median(lat)), 2)}

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "prep": stage_prep()
    elif cmd == "origin": stage_origin(int(sys.argv[2]))
    elif cmd == "memory": stage_memory()
    elif cmd == "arms": stage_arms(sys.argv[2])
    elif cmd == "summary": stage_summary()
    elif cmd == "twin": stage_twin()
    elif cmd == "seeds": stage_seeds()
