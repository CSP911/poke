#!/usr/bin/env python3
"""plan — a purpose, spoken to the hub, carried out over several edges (L4 of docs/design/hub-registry.md).

    observe   the model reads the body summary (capabilities only) and plans read steps → the hub checks them
    run       per step: the model writes op_main against cap_*() for exactly the capabilities granted; the hub
              generates that image's poke_body.h, builds, boots it on that node (each node its own body)
    decide    the model sees the results and either answers, or plans write steps (checked again; writes the
              owner's policy marks 'confirm' are asked about) → run → answer

    ../sim/body/.venv/bin/python plan.py --yes "두 방 중 더 더운 방의 조명을 켜줘"
"""
import os, sys, re, json, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "sim", "body"))
import registry as R

PLAN_SYSTEM = """You plan how a home hub carries out a person's purpose with the machines it has. You do not see chips or code — only places, nodes and capability instances (ids). Reply with ONE JSON object and nothing else.

Observation round — reply:
{"steps": [{"id": "s1", "on": "<node id>", "uses": ["<capability id>", ...], "do": "<what this step does, one line>"}, ...],
 "why": "<one line: why these capabilities>"}
Use only capability ids from the summary, each on the node it belongs to; read only what the purpose needs. Steps run at once, one boot each.
A reading step may add "repeat": {"every_min": 5–240, "times": 2–24} when the purpose is about how something changes over time (e.g. "every evening", "why does it get hot") — the hub boots it on that schedule and you get the series with the clock of each reading.
If the purpose cannot be served by what exists, reply {"cannot": "<Korean: what is missing>"} instead.
If the purpose is an action that waits for something to happen ("~하면 …해줘", "when I leave, …"), reply a standing intent instead of steps:
{"standing": {"watch": {"id": "w1", "on": "<node>", "uses": ["<reading capability id>"], "do": "<what to read, report it under a fixed key>"},
              "every_min": 5–240, "until_min": 10–1440,
              "when": {"key": "<the key the watch step reports>", "op": "==|!=|<|<=|>|>=", "value": <number>, "times": <readings in a row, 1–12>},
              "then": [{"id": "a1", "on": "<node>", "uses": ["<capability id>"], "do": "<exact action>"}],
              "say_ko": "<Korean: what you set up, as it will read after the person agrees>"}}
Choose "times" so one stray reading does not trigger the action (e.g. nobody moving for three readings, not one).

Decision round (you get the results) — reply either
{"answer_ko": "<Korean answer to the person>", "steps": []}
or, when the purpose asks for an action, also act steps:
{"answer_ko": "...", "steps": [{"id": "a1", "on": "<node>", "uses": ["<capability id>"], "do": "<exact action, e.g. turn channel 0 on>"}]}
Never act beyond what the person asked. If the action the purpose needs has no capability, say so in answer_ko and add no steps.
Never guess a write's target: if the purpose does not say which channel and the owner's channel names do not settle it (e.g. "the light" and exactly one channel is named as a light), reply {"ask_ko": "<one Korean question>", "steps": []} instead.
Write answer_ko as it will read AFTER the steps have run (the hub runs them, asking the person first when the policy says confirm): "켰습니다", not "켜겠습니다", and do not ask for approval yourself."""

OP_SYSTEM = """You write one C function for a machine with no operating system: void op_main(void). Output ONLY C code, no markdown.
Available: #include "rt.h" is already done — say(const char*), report_int(key,long), report_fixed(key,long v,int decimals), report_str(key,const char*), fail(const char*), ms(), delay_ms(n<=10000).
The device functions are declared for you (do not declare them); call them only with the constants listed. Each returns 0 on success; on error call fail("...") and return.
Report every value the step needs under short snake_case keys; temperatures as report_fixed(key, milli_c, 3). No other functions, no globals beyond small arrays, no floats."""

def _ask(system, user):
    import claude_cli
    return claude_cli.ask(system, user).strip()

def _json(text):
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None

def plan_round(prompt, rounds=3, log=print):
    for r in range(rounds):
        p = _json(_ask(PLAN_SYSTEM, prompt))
        if p is None:
            prompt += "\n\nYour reply was not one JSON object. Reply with the JSON only."; continue
        if "cannot" in p or "standing" in p and not p.get("steps"):
            probs = R.validate_standing(p["standing"]) if "standing" in p else []
            if not probs: return p
        elif not p.get("steps"):
            return p
        else:
            probs = R.validate_plan(p)
        if not probs:
            return p
        log(f"[계획] 허브가 돌려보냄: {'; '.join(probs)}")
        prompt += "\n\nThe hub rejected that plan:\n- " + "\n- ".join(probs) + "\nFix it."
    raise RuntimeError("no valid plan after 3 rounds")

def run_step(step, tag, log=print, rounds=3):
    inst = {i["id"]: i for i in R.instances(step["on"])}
    granted = [inst[u] for u in step["uses"]]
    C = R.caps()
    lines = []
    for cap in sorted({g["cap"] for g in granted}):
        lines.append(f"{C[cap]['call']};   // {C[cap]['describes_ko']}")
    consts = "\n".join(f"  {g['const']}  — {g['place']} {g['label']} ({g['cap']}, spec {json.dumps(g['spec'], ensure_ascii=False)})" for g in granted)
    prompt = f"Step: {step['do']}\nNode: {step['on']} ({granted[0]['place']})\n\nDevice functions:\n" + "\n".join(lines) + f"\n\nConstants you may use:\n{consts}\n"
    for r in range(rounds):
        src = _ask(OP_SYSTEM, prompt)
        src = re.sub(r"^```[a-z]*\n|\n```\s*$", "", src.strip())
        try:
            img, full, allow = R.build_step(step["on"], step["uses"], src, f"plan-{tag}-{step['id']}", summary_ko=step["do"])
            _IMG[(step["on"], tuple(step["uses"]), tag)] = img
            break
        except RuntimeError as e:
            log(f"   [{step['id']}] 빌드 실패, 다시: {str(e).splitlines()[-1][:160]}")
            prompt += f"\n\nThat did not compile:\n{str(e)[-800:]}\nWrite op_main again."
    else:
        return {"id": step["id"], "error": "op did not build"}
    rp = step.get("repeat")
    if rp:
        series = []
        for k in range(rp["times"]):
            if k: _wait(rp["every_min"])
            b = R.boot_step(step["on"], img)
            series.append({"at": (b.get("world") or {}).get("clock") or time.strftime("%H:%M"), **(b.get("result") or {})})
        log(f"   [{step['id']}] {rp['times']}회 관측 ({rp['every_min']}분 간격), 같은 이미지 재사용")
        return {"id": step["id"], "on": step["on"], "result": {"series": series}, "powered_off": b.get("powered_off"),
                "wipe_nonzero": (b.get("wipe_check") or {}).get("nonzero"), "truth": {}}
    b = R.boot_step(step["on"], img)
    w = (b.get("wipe_check") or {})
    return {"id": step["id"], "on": step["on"], "result": b.get("result"), "at": (b.get("world") or {}).get("clock"), "powered_off": b.get("powered_off"),
            "wipe_nonzero": w.get("nonzero"), "effects": (b.get("effects") or {}).get("actions") if isinstance(b.get("effects"), dict) else None,
            "truth": {k: v for k, v in (b.get("truth") or {}).items() if k != "ext"} | {k: v for k, v in ((b.get("truth") or {}).get("ext") or {}).items() if k != "screen"}}

def run_all(steps, tag, log=print):
    """Steps of one round. Repeating steps on the same schedule are booted side by side at each tick (one
    clock for all of them: the series line up), then time passes once."""
    once = {s["id"]: run_step(s, tag, log) for s in steps if not s.get("repeat")}
    reps = [s for s in steps if s.get("repeat")]
    if reps:
        every, times = min(s["repeat"]["every_min"] for s in reps), max(s["repeat"]["times"] for s in reps)
        if any(s["repeat"]["every_min"] != every or s["repeat"]["times"] != times for s in reps):
            log(f"   [허브] 반복 일정을 하나로 맞춤: {every}분 간격 {times}회")
        series = {s["id"]: [] for s in reps}
        for k in range(times):
            if k: _wait(every)
            for s in reps:
                o = run_step({**s, "repeat": None}, tag, log) if k == 0 else _boot_again(s, tag)
                series[s["id"]].append({"at": o.get("at") or time.strftime("%H:%M"), **(o.get("result") or {})})
        log(f"   [반복] {len(reps)}개 단계 × {times}회 ({every}분 간격), 단계마다 이미지는 한 번만 빌드")
        for s in reps:
            once[s["id"]] = {"id": s["id"], "on": s["on"], "result": {"series": series[s["id"]]}}
    return [once[s["id"]] for s in steps]

def _wait(minutes):
    """Time passing between two boots of a schedule: the virtual home advances; a real one would be the hub's
    timer (not here yet — the hub refuses long waits rather than block)."""
    if R.world_on():
        import world as W
        w = W.load(); W.advance(w, minutes); W.save(w)
    else:
        raise RuntimeError("waiting between boots needs the hub's timer — only the virtual home (POKE_WORLD) can do it now")

def run_standing(st, yes=False, log=print, tag="st"):
    """A standing intent: the watch image is built once and booted every N minutes; the action runs once."""
    need = R.confirmations({"steps": st["then"]})
    log(f"[상시 의도] {st['watch']['do']} — {st['every_min']}분마다, 최대 {st['until_min']}분; 조건 {json.dumps(st['when'], ensure_ascii=False)}")
    for a in st["then"]: log(f"   그때 {a['id']} @{a['on']}: {a['do']}  ← {', '.join(a['uses'])}")
    if need and not yes:
        log(f"[허브] 확인이 필요한 행동입니다({', '.join(need)}). --yes로 등록하세요."); return {"standing": st, "registered": False}
    c, hits, seen, waited = st["when"], 0, [], 0
    first = run_step({**st["watch"], "repeat": None}, tag, log)
    while True:
        o = first if first else None
        if o is None:
            _wait(st["every_min"]); waited += st["every_min"]
            o = _boot_again(st["watch"], tag)
        first = None
        v = (o.get("result") or {}).get(c["key"])
        seen.append({"at": o.get("at"), c["key"]: v})
        hits = hits + 1 if isinstance(v, (int, float)) and R.WHEN_OPS[c["op"]](v, c["value"]) else 0
        log(f"   [{o.get('at')}] {c['key']}={v} · 연속 {hits}/{c.get('times', 1)}")
        if hits >= c.get("times", 1):
            done = [run_step(a, tag, log) for a in st["then"]]
            for d in done: log(f"   [{d['id']} @{d.get('on')}] {json.dumps(d.get('result'), ensure_ascii=False)} · 실험대 {json.dumps(d.get('truth'), ensure_ascii=False)[:200]}")
            return {"standing": st, "fired_at": o.get("at"), "seen": seen, "done": done}
        if waited + st["every_min"] > st["until_min"]:
            log("[상시 의도] 기한이 지나 조건 없이 끝났습니다."); return {"standing": st, "fired_at": None, "seen": seen}

_IMG = {}
def _boot_again(step, tag):
    """The watch step's image is the same every time — build once (run_step keeps it), boot again."""
    img = _IMG.get((step["on"], tuple(step["uses"]), tag))
    if img is None: return run_step({**step, "repeat": None}, tag)
    b = R.boot_step(step["on"], img)
    return {"id": step["id"], "on": step["on"], "result": b.get("result"), "at": (b.get("world") or {}).get("clock")}

def run(purpose, yes=False, log=print):
    t0 = time.time()
    log(f"\n🗣  \"{purpose}\"")
    summary = R.body_summary()
    p = plan_round(f"Purpose: {purpose}\n\nThe body (places, nodes, capability instances):\n{summary}\n\nThis is the observation round.", log=log)
    if "cannot" in p:
        log(f"[허브] 할 수 없습니다: {p['cannot']}"); return p
    if "standing" in p:
        r = run_standing(p["standing"], yes=yes, log=log, tag=hex(abs(hash(purpose)) % (1 << 24))[2:])
        log(f"[답] {p['standing'].get('say_ko', '')}" + (f" — {r['fired_at']}에 실행했습니다." if r.get("fired_at") else ""))
        log(f"[허브] 전체 {round(time.time() - t0, 1)} s"); return r
    log(f"[계획] {p.get('why', '')}")
    for s in p["steps"]: log(f"   {s['id']} @{s['on']}: {s['do']}  ← {', '.join(s['uses'])}")
    tag = hex(abs(hash(purpose)) % (1 << 24))[2:]
    obs = run_all(p["steps"], tag, log)
    for o in obs: log(f"   [{o['id']} @{o.get('on')}] {json.dumps(o.get('result'), ensure_ascii=False)} · 꺼짐 {o.get('powered_off')} · 비움 {o.get('wipe_nonzero')}")
    d = plan_round(f"Purpose: {purpose}\n\nThe body:\n{summary}\n\nObservation steps and their results:\n"
                   + json.dumps([{"step": s, "result": o.get("result"), "at": o.get("at")} for s, o in zip(p["steps"], obs)], ensure_ascii=False, indent=1)
                   + "\n\nThis is the decision round.", log=log)
    if d.get("ask_ko"):
        log(f"[허브] 되물음: {d['ask_ko']}"); return d
    acts = d.get("steps") or []
    if acts:
        need = R.confirmations(d)
        for s in acts: log(f"[행동 계획] {s['id']} @{s['on']}: {s['do']}  ← {', '.join(s['uses'])}")
        if need and not yes:
            log(f"[허브] 확인이 필요한 행동입니다({', '.join(need)}). --yes로 실행하세요."); return d
        done = [run_step(s, tag, log) for s in acts]
        for o in done: log(f"   [{o['id']} @{o.get('on')}] {json.dumps(o.get('result'), ensure_ascii=False)} · 꺼짐 {o.get('powered_off')} · 비움 {o.get('wipe_nonzero')} · 실험대 {json.dumps(o.get('truth'), ensure_ascii=False)}")
        d["done"] = done
    log(f"[답] {d.get('answer_ko', '')}")
    log(f"[허브] 전체 {round(time.time() - t0, 1)} s")
    d["observed"] = obs
    return d

if __name__ == "__main__":
    a = sys.argv[1:]; yes = "--yes" in a; a = [x for x in a if x != "--yes"]
    if a: run(" ".join(a), yes=yes)
    else: print(__doc__)
