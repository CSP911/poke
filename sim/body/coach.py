"""Voice coaching: a person watches the body and talks; the body learns.

    .venv/bin/mjpython coach.py ["주먹 쥐어봐"] [--auto] [--no-mic]

The language model writes a skill for the finger and the intent. The finger
tries it, attempt after attempt, in a 3D window. While it moves, a person
comments out loud ("움직였어", "검지 말아쥐었어", "오히려 풀렸어", "쥐었어").

  fast loop  every attempt: comments are scored (+ / -) and the skill's
             numbers move toward what got praised, away from what got scolded
  slow loop  when the numbers stop helping, the model rewrites the skill's
             structure from the transcript and the joint log
  stop       "멈춰" / "그만" freezes the finger at once, without the model
  done       "그거야" / "됐어" saves the skill to the library

Speech is assumed to lag what it describes by LAG seconds. Comments can
also be typed into build/say.txt (one line each). --auto replaces the
person with a simulated watcher that sees the true fingertip position.
"""
import ctypes, json, math, os, queue, random, re, subprocess, sys, threading, time
import numpy as np
import mujoco, mujoco.viewer
import sim, finger, pokeedge

HERE = sim.HERE
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
LOG = os.path.join(sim.BUILD, "coach.jsonl")
SAY = os.path.join(sim.BUILD, "say.txt")
LAG = 0.8
T_SKILL, T_HOLD, T_RELAX, T_LISTEN = 3.0, 1.0, 1.0, 1.5
MAX_ATTEMPTS = 20
STALL = 4                      # attempts without improvement → rewrite the structure

os.makedirs(sim.BUILD, exist_ok=True)
for line in open(os.path.join(ROOT, ".env")):
    m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
    if m and m.group(1) not in os.environ:
        os.environ[m.group(1)] = m.group(2)

def log(kind, **kw):
    kw.update(kind=kind, t=time.time())
    with open(LOG, "a") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")

# ── understanding a comment ──────────────────────────────────────────────
STOP = re.compile(r"멈춰|그만|스톱|정지|stop", re.I)
RESUME = re.compile(r"계속|다시 해|해 봐|해봐|go", re.I)
DONE = re.compile(r"그거야|됐어|완벽|바로 그거|성공|그게 주먹")
NEG = re.compile(r"풀렸|풀려|아니|반대|오히려|틀렸|별로|안 ?돼|펴졌|펴지|멀어")
NEGATED_POS = re.compile(r"안 ?(쥐|말|움직|굽)")
POS_STRONG = re.compile(r"쥐었|쥐어졌|주먹|닿았")
POS = re.compile(r"좋아|그렇지|말았|말아|굽혔|접었|잘했|맞아|옳지|오오|더 ?(가|해|쥐|말|굽)|조금 더|아까보다 (더 )?(말|쥐|굽)")
MOVED = re.compile(r"움직|돌았|까딱")

def rules(text):
    t = text.replace(" ", " ")
    r = {"stop": bool(STOP.search(t)), "resume": bool(RESUME.search(t)) and not STOP.search(t),
         "done": bool(DONE.search(t)), "polarity": 0.0}
    if NEGATED_POS.search(t) or NEG.search(t):
        r["polarity"] = -1.5
    elif POS_STRONG.search(t):
        r["polarity"] = 2.0
    elif POS.search(t):
        r["polarity"] = 1.0
    elif MOVED.search(t):
        r["polarity"] = 0.5
    return r

_haiku = None
def haiku_classify(text, intent):
    """Second opinion from a fast model, for comments the rules don't cover."""
    global _haiku
    try:
        if _haiku is None:
            import anthropic
            _haiku = anthropic.Anthropic()
        msg = _haiku.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=120,
            system=("A person watches a robot finger try to do: " + intent + ". They comment in Korean. "
                    "Return ONLY JSON {\"polarity\": number from -2 (getting worse) to 2 (achieved), "
                    "\"stop\": bool, \"done\": bool, \"part\": string or null (body part they named)}."),
            messages=[{"role": "user", "content": text}])
        s = msg.content[0].text
        return json.loads(s[s.index("{"): s.rindex("}") + 1])
    except Exception as e:
        return {"error": str(e)[:120]}

# ── ears: microphone, typed file, simulated watcher ─────────────────────
class Ears:
    def __init__(self, intent, mic=True):
        self.q, self.intent, self.all = queue.Queue(), intent, []
        self.stop_flag = threading.Event()
        self.proc = None
        if mic:
            self.proc = subprocess.Popen(["node", os.path.join(HERE, "listen.js")], stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, bufsize=1)
            threading.Thread(target=self._mic, daemon=True).start()
        open(SAY, "a").close()
        threading.Thread(target=self._file, daemon=True).start()

    def _mic(self):
        for line in self.proc.stdout:
            try:
                u = json.loads(line)
            except Exception:
                continue
            if "text" in u:
                self.hear(u["text"], u["t0"] / 1000.0, "mic")

    def _file(self):
        with open(SAY) as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.1); continue
                if line.strip():
                    self.hear(line.strip(), time.time(), "typed")

    def hear(self, text, t0, src):
        r = rules(text)
        u = {"text": text, "t0": t0, "src": src, **r}
        if r["stop"]:
            self.stop_flag.set()                       # fast path: no model in the loop
        if r["resume"]:
            self.stop_flag.clear()
        print(f"   🗣  [{src}] {text}   → {'+' if u['polarity'] > 0 else ''}{u['polarity']:g}"
              f"{'  STOP' if r['stop'] else ''}{'  DONE' if r['done'] else ''}", flush=True)
        if r["polarity"] == 0 and not (r["stop"] or r["done"] or r["resume"]):
            h = haiku_classify(text, self.intent)       # unknown phrasing: ask the fast model
            if "polarity" in h:
                u["polarity"] = float(h["polarity"]); u["done"] = bool(h.get("done")); u["part"] = h.get("part")
                print(f"      (haiku: {h})", flush=True)
        log("utterance", **u)
        self.all.append(u)
        self.q.put(u)

    def window(self, a, b):
        """utterances describing what happened between wall times a and b"""
        return [u for u in self.all if a <= u["t0"] - LAG <= b]

    def close(self):
        if self.proc:
            self.proc.terminate()

class AutoWatcher:
    """Stand-in for a person: sees the true fingertip and talks like one, LAG s late."""
    def __init__(self, ears):
        self.ears, self.pending, self.held = ears, [], 0
        self.reset()
    def reset(self):
        self.start_gap = None; self.best = None; self.said = set(); self.last_gap = None
        self.prev_best = getattr(self, "prev_best", None)
    def say(self, text):
        self.pending.append((time.time() + LAG, text, time.time()))
    def see(self, m, d, phase):
        now = time.time()
        for p in [p for p in self.pending if p[0] <= now]:
            self.pending.remove(p); self.ears.hear(p[1], p[2], "auto")
        if phase != "skill":
            return
        gap, touch = finger.tip_palm_gap(m, d), finger.tip_touches_palm(m, d)
        q = d.qpos[:3]
        curled = all(x < -1.2 for x in q) and -q.sum() > 3.9        # every joint past ~70°, 225° in all
        if self.start_gap is None:
            self.start_gap, self.best, self.last_gap = gap, gap, gap; return
        if (touch or curled) and "fist" not in self.said:
            self.said.add("fist"); self.say("오 쥐었어!"); return
        if gap < self.best - 0.015 and "moved" not in self.said:
            self.said.add("moved"); self.say("오 지금 손가락 움직였어")
        if gap < 0.05 and "curl" not in self.said:
            self.said.add("curl"); self.say("오 검지 말아쥐었어")
        if gap > self.best + 0.02 and "worse" not in self.said:
            self.said.add("worse"); self.say("오히려 풀렸어")
        self.best = min(self.best, gap); self.last_gap = gap
    def end_attempt(self, m, d):
        touched = "fist" in self.said
        self.held = self.held + 1 if touched else 0
        prev = getattr(self, "prev_best", None)
        if self.held >= 2:
            self.say("그거야! 그게 주먹이야")
        elif not self.said or (self.best is not None and self.best > 0.25):
            self.say("아니야 반대야")
        elif prev is not None and self.best < prev - 0.005:
            self.say("오 아까보다 더 말았어 좋아")
        elif prev is not None and self.best > prev + 0.005:
            self.say("아까보다 풀렸어")
        else:
            self.say("조금 더 쥐어")
        self.prev_best = self.best
        self.reset()

# ── skill generation (slow loop) ────────────────────────────────────────
def generate(intent, tag, feedback=None):
    bpath = os.path.join(sim.BUILD, "finger-body.json")
    json.dump(finger.BODY, open(bpath, "w"), ensure_ascii=False)
    src = os.path.join(HERE, "skills", f"gen-coach-{tag}.c")
    cmd = ["node", os.path.join(HERE, "generate.js"), bpath, intent, src] + ([feedback] if feedback else [])
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:300])
    meta = json.loads(r.stdout.strip().splitlines()[-1])
    lib = sim.compile_skill(src, f"gen-coach-{tag}")
    return lib, meta, src, time.time() - t0

class PokeBrain:
    """The skill runs on POKE: built for aarch64, injected as an EL0 unit,
    stepped over UDP; the kernel's safety filter decides what comes back."""
    where = "POKE (EL0 unit, kernel safety filter)"
    def __init__(self, src, edge):
        self.edge = edge                                  # one link per session (the twin accepts one)
        self.size = len(img := pokeedge.build(src, "coach-skill"))
        self.edge.load(img, [finger.LIMIT] * 3, [0, 0, 0])
        self.ev = {}
    def attempt(self, params):
        self.edge.attempt(params)
    def step(self, t, tick, q):
        cmd, self.ev = self.edge.step(t, tick, q)
        return np.array(cmd)
    def close(self):
        self.edge.stop()

class LocalBrain:
    """Mac-only comparison path: the skill as a native library, Python safety."""
    where = "Mac (local, comparison only)"
    def __init__(self, lib_path):
        self.lib = ctypes.CDLL(lib_path)
        self.lib.skill_step.argtypes = [ctypes.POINTER(sim.MotionIO)]
        self.io = sim.MotionIO(); self.io.njoints = 3; self.size = os.path.getsize(lib_path)
        for j in range(3):
            self.io.limit[j] = finger.LIMIT
        self.safety = None; self.ev = {}
    def attempt(self, params):
        for i in range(8):
            self.io.param[i] = float(params[i]) if i < len(params) else 0.0
        self.safety = None
    def step(self, t, tick, q):
        if self.safety is None:
            self.safety = sim.Safety(3, finger.LIMIT); self.safety.prev = np.array(q, dtype=float)
        self.io.t, self.io.tick = t, tick
        for j in range(3):
            self.io.q[j] = q[j]
        self.lib.skill_step(ctypes.byref(self.io))
        out = self.safety.filter([self.io.cmd[j] for j in range(3)], 0.0, 1.0 / sim.HZ)
        self.ev = self.safety.events
        return out
    def close(self):
        pass

EDGE = {"host": None, "twin": None}
def edge_host():
    """POKE_HOST: unset/'twin' → boot the QEMU twin once; an IP → the real Pi."""
    if EDGE["host"] is None:
        h = os.environ.get("POKE_HOST", "twin")
        if h == "twin":
            EDGE["twin"] = pokeedge.Twin(); EDGE["host"] = EDGE["twin"].__enter__()
            print(f"[POKE] QEMU 트윈 부팅 완료: {EDGE['host']}", flush=True)
        else:
            EDGE["host"] = h
    return EDGE["host"]

def make_brain(src, lib_path):
    if "--local" in sys.argv:
        return LocalBrain(lib_path)
    if EDGE.get("skill") is None:
        EDGE["skill"] = pokeedge.PokeSkill(edge_host())
    return PokeBrain(src, EDGE["skill"])

# ── the session ─────────────────────────────────────────────────────────
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    auto, mic = "--auto" in sys.argv, ("--no-mic" not in sys.argv and "--auto" not in sys.argv)
    intent = args[0] if args else "주먹 쥐어봐 (curl the finger into a fist, toward the palm)"
    open(LOG, "w").close()
    print(f"\n의도: {intent}\n[기술 생성] 언어 모델이 이 손가락을 위한 기술을 쓰는 중...", flush=True)
    lib_path, meta, src, dt = generate(intent, "1")
    print(f"   → '{meta['name']}' ({dt:.0f}s): {meta.get('idea','')}")
    print(f"     조절할 숫자: {', '.join(p['name'] + f' [{p['lo']}, {p['hi']}]' for p in meta['params'])}", flush=True)
    log("skill", name=meta["name"], idea=meta.get("idea"), params=meta["params"], round=1)
    brain = make_brain(src, lib_path)
    print(f"[실행 위치] {brain.where} — 기술 이미지 {brain.size} B", flush=True)
    ranges = [(float(p["lo"]), float(p["hi"])) for p in meta["params"]]

    m = finger.model(); d = mujoco.MjData(m)
    ears = Ears(intent, mic=mic)
    watcher = AutoWatcher(ears) if auto else None
    if mic:
        print("🎤 마이크 켜짐 — 손가락을 보면서 편하게 말해 줘. ('멈춰'는 즉시 정지)", flush=True)
    print(f"⌨  글로 말하려면: echo \"쥐었어\" >> {SAY}\n", flush=True)

    best = np.array([(lo + hi) / 2 for lo, hi in ranges]); best_score = -1e9
    cand = best.copy(); stall, rnd, attempt = 0, 1, 0
    rng = np.random.default_rng(1)
    io = sim.MotionIO(); io.njoints = 3
    for j in range(3):
        io.axis[j] = 0; io.limit[j] = finger.LIMIT
    safety = sim.Safety(3, finger.LIMIT)
    sub = int(round(1.0 / sim.HZ / m.opt.timestep))
    success_feats = []

    with mujoco.viewer.launch_passive(m, d, show_left_ui=False, show_right_ui=False) as v:
        v.cam.azimuth, v.cam.elevation, v.cam.distance = 90, -20, 0.32
        v.cam.lookat[:] = [-0.01, 0, 0.03]
        done = False
        while v.is_running() and attempt < MAX_ATTEMPTS and not done:
            attempt += 1
            try:
                brain.attempt(list(cand))
            except pokeedge.EdgeError as e:
                print(f"   ✗ POKE: {e}", flush=True); break
            print(f"── 시도 {attempt} (라운드 {rnd}) params "
                  + ", ".join(f"{p['name']}={x:+.2f}" for p, x in zip(meta["params"], cand)), flush=True)
            t_start = time.time(); trace = []; crashed = None
            phase_plan = [("skill", T_SKILL + T_HOLD), ("relax", T_RELAX), ("listen", T_LISTEN)]
            k = 0
            for phase, dur in phase_plan:
                t_phase = time.time()
                relax_from = d.qpos[:3].copy()
                while time.time() - t_phase < dur and v.is_running():
                    tick0 = time.time()
                    if ears.stop_flag.is_set():
                        status = "STOPPED - say '계속' to go on"
                        target = safety.prev.copy()
                    elif phase == "skill":
                        try:
                            target = brain.step(time.time() - t_phase, k, d.qpos[:3].tolist()); k += 1
                        except pokeedge.EdgeError as e:            # skill crashed or hung: the Pi stays up
                            print(f"   ✗ 기술이 POKE에서 멈춤: {e}", flush=True)
                            log("skill_fault", error=str(e)); crashed = str(e); target = d.qpos[:3].copy(); break
                        safety.prev = np.array(target)
                        status = f"attempt {attempt}: trying (on {'POKE' if isinstance(brain, PokeBrain) else 'Mac'})"
                    elif phase == "relax":
                        a = min(1.0, (time.time() - t_phase) / dur)
                        target = safety.filter(relax_from * (1 - a), 0.0, 1.0 / sim.HZ)
                        status = f"attempt {attempt}: relaxing"
                    else:
                        target = safety.prev.copy(); status = f"attempt {attempt}: listening"
                    d.ctrl[:3] = target
                    for _ in range(sub):
                        mujoco.mj_step(m, d)
                    load = float(np.abs(d.actuator_force[:3]).sum())
                    trace.append((time.time(), d.qpos[:3].tolist(), load, finger.tip_touches_palm(m, d), phase))
                    if watcher:
                        watcher.see(m, d, phase)
                    last = ears.all[-1]["text"] if ears.all else ""
                    v.set_texts([(mujoco.mjtFontScale.mjFONTSCALE_150, mujoco.mjtGridPos.mjGRID_TOPLEFT,
                                  "POKE coach", f"{meta['name']}\n{status}"),
                                 (mujoco.mjtFontScale.mjFONTSCALE_150, mujoco.mjtGridPos.mjGRID_BOTTOMLEFT,
                                  "best score", f"{max(best_score, 0):.1f}")])
                    v.sync()
                    time.sleep(max(0.0, 1.0 / sim.HZ - (time.time() - tick0)))
            if watcher:
                watcher.end_attempt(m, d)
                t_wait = time.time()
                while watcher.pending and time.time() - t_wait < 2:
                    watcher.see(m, d, "listen"); time.sleep(0.05)
            time.sleep(0.3)
            heard = ears.window(t_start, time.time())
            score = sum(u["polarity"] for u in heard)
            done = any(u.get("done") for u in heard)
            skill_part = [x for x in trace if x[4] == "skill"] or trace
            end_q = skill_part[-1][1]
            touched = any(x[3] for x in trace)
            log("attempt", n=attempt, round=rnd, params=cand.tolist(), score=score,
                heard=[u["text"] for u in heard], end_q=end_q, touched=touched)
            for u in heard:                          # what the body felt when it was told "you did it"
                if u["polarity"] >= 2:
                    tt = u["t0"] - LAG
                    near = min(trace, key=lambda x: abs(x[0] - tt))
                    success_feats.append((near[2], near[3]))
            print(f"   점수 {score:+.1f}  (끝 자세 " + ", ".join(f"{math.degrees(q):+.0f}°" for q in end_q) + ")", flush=True)
            if done:
                break
            # fast loop: keep what was praised, explore around it
            step = np.zeros(len(best))
            # comments are relative ("아까보다 더"), so an equally praised attempt also counts as progress
            if score > best_score or (score > 0 and score >= best_score):
                step = cand - best if best_score > -1e9 else np.zeros(len(best))   # what just helped
                best_score, best, stall = score, cand.copy(), 0
            else:
                stall += 1
            sigma = np.array([(hi - lo) * (0.25 if stall < 2 else 0.12) for lo, hi in ranges])
            cand = np.clip(best + 1.0 * step + rng.normal(0, 1, len(best)) * sigma,
                           [lo for lo, _ in ranges], [hi for _, hi in ranges])
            if score < 0:                               # scolded: try the other way for direction-like params
                for i, (lo, hi) in enumerate(ranges):
                    if lo < 0 < hi and abs(lo + hi) < 1e-6 and rng.random() < 0.5:
                        cand[i] = -best[i] if abs(best[i]) > 0.2 else (hi if rng.random() < .5 else lo)
            if crashed:
                stall = STALL                                # rewrite now, and say why
            # slow loop: rewrite the structure from what was said
            if stall >= STALL:
                rnd += 1
                transcript = [{"attempt": e["n"], "params": dict(zip([p["name"] for p in meta["params"]],
                               [round(x, 2) for x in e["params"]])), "end_angles_deg": [round(math.degrees(q)) for q in e["end_q"]],
                               "person_said": e["heard"], "score": e["score"]}
                              for e in map(json.loads, open(LOG)) if e["kind"] == "attempt"][-8:]
                fb = (("The skill FAILED on the device: " + crashed + ". " if crashed else "") +
                      "Live coaching transcript (a person watched each attempt and spoke; scores from their words): "
                      + json.dumps(transcript, ensure_ascii=False)
                      + " Rewrite the skill's STRUCTURE so it does what the person wants.")
                prev_names, prev_best = [p["name"] for p in meta["params"]], best.tolist()
                fb += (" Best praised params so far (keep their meaning and names if you keep the param, "
                       "and make them the defaults): " + json.dumps(dict(zip(prev_names, [round(x, 2) for x in prev_best]))))
                print(f"\n[기술 다시 쓰기] 피드백이 더 이상 늘지 않아 언어 모델이 대화 기록을 보고 구조를 다시 씀...", flush=True)
                try:
                    lib_path, meta, src, dt = generate(intent, str(rnd), fb)
                    brain.close(); brain = make_brain(src, lib_path)
                    ranges = [(float(p["lo"]), float(p["hi"])) for p in meta["params"]]
                    learned = dict(zip(prev_names, prev_best))
                    best = np.array([learned.get(p["name"], (lo + hi) / 2) for p, (lo, hi) in zip(meta["params"], ranges)])
                    best = np.clip(best, [lo for lo, _ in ranges], [hi for _, hi in ranges]); cand = best.copy()
                    best_score, stall = -1e9, 0
                    print("     배운 값 이어받음: " + ", ".join(f"{k}={v:+.2f}" for k, v in learned.items()
                                                      if k in [p["name"] for p in meta["params"]]), flush=True)
                    print(f"   → '{meta['name']}' ({dt:.0f}s): {meta.get('idea','')}", flush=True)
                    log("skill", name=meta["name"], idea=meta.get("idea"), params=meta["params"], round=rnd)
                except RuntimeError as e:
                    print(f"   ✗ {e}", flush=True); stall = 0

        # results
        if done:
            libdir = os.path.join(sim.BUILD, "library"); os.makedirs(libdir, exist_ok=True)
            name = re.sub(r"[^a-z0-9-]", "", meta["name"].lower()) or "fist"
            with open(src) as f_src, open(os.path.join(libdir, f"{name}.c"), "w") as f_out:
                f_out.write(f_src.read())
            json.dump({"intent": intent, "params": dict(zip([p["name"] for p in meta["params"]], cand.tolist())),
                       "attempts": attempt}, open(os.path.join(libdir, f"{name}.json"), "w"), ensure_ascii=False, indent=2)
            print(f"\n✦ 완성: '{meta['name']}'를 {attempt}번 시도 만에 배웠어 → build/library/{name}.c", flush=True)
        if success_feats:
            loads = [f[0] for f in success_feats]; touch = sum(1 for f in success_feats if f[1])
            print(f"✦ '쥐었어'라고 들었을 때 몸의 느낌: 서보 부하 평균 {np.mean(loads):.2f} N·m, "
                  f"손바닥 접촉 {touch}/{len(success_feats)}회 → 다음엔 이 느낌으로 스스로 판단할 수 있어", flush=True)
        log("end", done=done, attempts=attempt)
        time.sleep(2)
    brain.close()
    ears.close()
    if EDGE["twin"]:
        EDGE["twin"].__exit__(None, None, None)

if __name__ == "__main__":
    try:
        main()
    finally:
        if EDGE.get("twin"):
            EDGE["twin"].__exit__(None, None, None)
