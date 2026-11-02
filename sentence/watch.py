"""watch — standing intents ("온도가 30도 넘으면 알려줘") kept by the hub, never by the node.

The node still boots once per check and is empty in between; the waiting is the hub's. Meeting decision 1
and the verify team's STANDING-CAPS (numbers in say.WATCH, one place):

  arm      the model answers WATCH: {"check": <one read-only sentence that reports hit=1/0>, "every_s",
           "then": <an action sentence or "">, "summary_ko"}. The check sentence becomes an ordinary
           record/op; say.watch_check(op) must pass (read functions only, policy allow, max_s ≤ 5). The
           watch image is built with the read-only allow list (so even a wrong op cannot act) and an RCFG
           budget of min(max_s·1000+1000, 5000) ms, and its hash is pinned. Arming is asked every time.
  run      due watches boot on the hub's schedule (≥ 60 s apart). After each boot say.watch_verdict →
           ok / strike / stop (2 bad in a row stops; a write or a refused transfer stops at once), and a
           body change, an i2c refusal or wipe_check nonzero stops it too. A changed record/op stops it.
  hit      the result's "hit" is true → a notice (screen, CLI, macOS notification if available). The watch
           is one-shot by default: it stops after the first hit. An action ("then") is NEVER run by the
           watch: it is left as a pending action that a person runs through the normal pipeline, with its
           own confirmation ('항상' approval does not apply to it).
  caps     per watch ≤ day_cap boots a day (then it rests until tomorrow), all watches ≤ total_day_cap,
           ≤ max_active armed at once, expires after ttl_h hours (re-arm = see the summary again).

Files: build/watches/<id>.json (one per watch, written atomically under record-style locks),
       build/watches/events.jsonl (hits, stops, pending actions).
"""
import contextlib, fcntl, hashlib, json, os, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("POKE_WATCHES", os.path.join(HERE, "build", "watches"))
_LOCAL = threading.local()

def _caps():
    import say
    return say.WATCH

def _now():
    return time.time()

def _day(t=None):
    return time.strftime("%Y-%m-%d", time.localtime(t or _now()))

# ── storage ────────────────────────────────────────────────────────────────
@contextlib.contextmanager
def _locked(name="all"):
    held = getattr(_LOCAL, "held", None)
    if held is None:
        held = _LOCAL.held = set()
    if name in held:
        yield; return
    os.makedirs(os.path.join(DIR, ".locks"), exist_ok=True)
    fd = os.open(os.path.join(DIR, ".locks", name + ".lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX); held.add(name)
        try: yield
        finally:
            held.discard(name); fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)

def _path(wid):
    return os.path.join(DIR, wid + ".json")

def _save(w):
    os.makedirs(DIR, exist_ok=True)
    tmp = f"{_path(w['id'])}.{os.getpid()}.{threading.get_ident()}.{os.urandom(3).hex()}.tmp"
    with open(tmp, "w") as f:
        json.dump(w, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path(w["id"]))
    return w

def load(wid):
    try:
        with open(_path(wid)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None

def all_watches():
    out = []
    if os.path.isdir(DIR):
        for f in sorted(os.listdir(DIR)):
            if f.endswith(".json"):
                w = load(f[:-5])
                if w: out.append(w)
    return out

def event(kind, w, text, **extra):
    os.makedirs(DIR, exist_ok=True)
    e = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, "watch": w.get("id"), "sentence": w.get("sentence"), "text": text, **extra}
    with _locked("events"), open(os.path.join(DIR, "events.jsonl"), "a") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return e

def events(n=50):
    try:
        with open(os.path.join(DIR, "events.jsonl")) as f:
            return [json.loads(l) for l in f.read().splitlines()[-n:] if l.strip()]
    except (OSError, ValueError):
        return []

def _sha(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()[:16]
    except OSError:
        return None

# ── arm ────────────────────────────────────────────────────────────────────
def plan(spec):
    """Validate the model's WATCH answer → (spec, problem_ko)."""
    caps = _caps()
    if not isinstance(spec, dict) or not isinstance(spec.get("check"), str) or not spec["check"].strip():
        return None, "무엇을 지켜볼지(점검 문장)를 정하지 못했습니다"
    try:
        every = float(spec.get("every_s") or caps["min_period_s"])
    except (TypeError, ValueError):
        every = caps["min_period_s"]
    return {"check": spec["check"].strip(), "every_s": max(every, caps["min_period_s"]),
            "then": (spec.get("then") or "").strip(), "summary_ko": (spec.get("summary_ko") or "").strip(),
            "repeat": bool(spec.get("repeat"))}, None

def prepare(sentence, spec, log=lambda *a: None):
    """Make the check op and the pinned watch image (no boot). → (draft, problem_ko)."""
    import say, rtcfg, manifest as MF
    p, prob = plan(spec)
    if prob:
        return None, prob
    try:
        op = say.make_op(p["check"], log=log)
    except Exception as e:
        import messages_ko as KO
        return None, "점검 op를 만들지 못했습니다: " + (getattr(e, "text", None) or KO.explain(e)[0])
    rec = op.get("record") or {}
    c = say.watch_check(op["src"])
    if not c["ok"]:
        return None, "; ".join(c["reasons"])
    m = MF.parse(op["src"]) or {}
    wid = "w-" + hashlib.sha1((sentence + str(_now())).encode()).hexdigest()[:8]
    img, obj, path = say.build(op["src"], "watch-" + wid, allow=c["allowlist"])
    budget = int(min(m.get("max_s", 5) * 1000 + 1000, 5000))
    data = rtcfg.patch(open(path, "rb").read(), budget_ms=budget)
    with open(path, "wb") as f:
        f.write(data)
    return {"id": wid, "sentence": sentence, **p, "check_summary": MF.summary(m, op.get("params"), rec.get("params")),
            "record": rec.get("id"), "record_op_sha": hashlib.sha1((rec.get("op") or op["src"]).encode()).hexdigest()[:16],
            "img": path, "img_sha": hashlib.sha1(data).hexdigest()[:16], "budget_ms": budget,
            "allowlist": c["allowlist"]}, None

def arm(draft):
    """Arm a prepared watch (the person said yes). → (watch, problem_ko)."""
    caps = _caps()
    with _locked():
        active = [w for w in all_watches() if w.get("state") in ("armed", "resting")]
        if len(active) >= caps["max_active"]:
            return None, f"이미 {len(active)}개를 지켜보는 중입니다 (동시에 {caps['max_active']}개까지) — 하나를 멈추고 다시 하세요"
        t = _now()
        w = {**draft, "state": "armed", "armed_at": t, "expires_at": t + caps["ttl_h"] * 3600, "next_at": t,
             "day": _day(t), "count_today": 0, "count": 0, "bad": 0, "last": None, "stop_reason": None}
        _save(w)
    event("armed", w, f"지켜보기 시작: {w['sentence']} ({w['every_s']:g}초마다, {caps['ttl_h']}시간 뒤 만료)")
    return w, None

def stop(wid, why="사람이 멈춤"):
    with _locked():
        w = load(wid)
        if not w or w.get("state") in ("stopped", "expired", "done"):
            return w
        w["state"], w["stop_reason"] = "stopped", why
        _save(w)
    event("stopped", w, f"멈춤: {why}")
    return w

def stop_all(why="사람이 모두 멈춤"):
    return [stop(w["id"], why) for w in all_watches() if w.get("state") in ("armed", "resting")]

def describe(w):
    caps = _caps()
    left = max(0, int(w.get("expires_at", 0) - _now()))
    return {"id": w["id"], "sentence": w["sentence"], "summary": w.get("summary_ko") or w.get("check_summary"),
            "every_s": w["every_s"], "state": w["state"], "count_today": w.get("count_today", 0), "day_cap": caps["day_cap"],
            "bad": w.get("bad", 0), "fault_stop": caps["fault_stop"], "last": w.get("last"), "then": w.get("then"),
            "expires_in_s": left, "stop_reason": w.get("stop_reason"), "next_in_s": max(0, int(w.get("next_at", 0) - _now()))}

# ── run ────────────────────────────────────────────────────────────────────
def _notify(text):
    """Best effort: a macOS notification (and a sound) on the hub. Never fails the watch."""
    import subprocess
    try:
        subprocess.run(["osascript", "-e", f'display notification {json.dumps(text)} with title "POKE" sound name "Glass"'],
                       capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass

def _hit(result):
    v = (result or {}).get("hit")
    return v is True or (isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0) or v in ("1", "true", "yes")

def _today_total():
    return sum(w.get("count_today", 0) for w in all_watches() if w.get("day") == _day())

def tick(bench=None, notify=True, log=print):
    """Run every watch that is due once. → list of events produced. Safe to call from several places."""
    import say, record as REC, messages_ko as KO, hub
    caps, out = _caps(), []
    if hub.DEMO["on"]:                                     # the demo runs on a copy of the memory: watches wait
        return out
    for w0 in all_watches():
        if w0.get("state") not in ("armed", "resting"):
            continue
        with _locked("w-" + w0["id"]):
            w = load(w0["id"])
            if not w or w.get("state") not in ("armed", "resting"):
                continue
            t = _now()
            if t >= w["expires_at"]:
                w["state"], w["stop_reason"] = "expired", f"{caps['ttl_h']}시간이 지나 만료 — 다시 지켜보려면 문장을 다시 말하세요"
                _save(w); out.append(event("expired", w, w["stop_reason"])); continue
            if w.get("day") != _day(t):
                w["day"], w["count_today"], w["state"] = _day(t), 0, "armed"
            if w["count_today"] >= caps["day_cap"]:
                if w["state"] != "resting":
                    w["state"] = "resting"; _save(w); out.append(event("resting", w, "오늘 상한에 닿아 내일까지 쉽니다"))
                continue
            if t < w.get("next_at", 0):
                continue
            with _locked():                                # the cap across all watches, counted under the global lock
                if _today_total() >= caps["total_day_cap"]:
                    continue
            # the pinned image and the record it came from must be the ones the person armed
            rec = REC.load(w["record"]) if w.get("record") else None
            if w.get("record") and rec is None:
                w["state"], w["stop_reason"] = "stopped", "점검 문장의 기록이 지워졌습니다 — 다시 말해서 무장하세요"
                _save(w); out.append(event("stopped", w, w["stop_reason"])); continue
            if _sha(w["img"]) != w["img_sha"] or (rec and hashlib.sha1((rec.get("op") or "").encode()).hexdigest()[:16] != w["record_op_sha"]):
                w["state"], w["stop_reason"] = "stopped", "점검 op나 이미지가 무장 뒤에 바뀌었습니다 — 다시 말해서 무장하세요"
                _save(w); out.append(event("stopped", w, w["stop_reason"])); continue
            w["next_at"] = t + w["every_s"]
            w["count_today"] += 1; w["count"] += 1
            _save(w)
        # boot outside the lock (a few hundred ms); the verdict goes back under it
        try:
            b = say.boot(w["img"], {**say.default_bench(), **(bench or {})})
        except Exception as e:
            b = {"lines": [], "result": None, "powered_off": False, "error": f"{type(e).__name__}: {e}"}
        verdict, why, bad = say.watch_verdict(b, w["img"], w.get("bad", 0))
        wc = b.get("wipe_check") or {}
        if verdict != "stop" and wc and wc.get("nonzero", 0) > 0:
            verdict, why = "stop", f"리셋 뒤 메모리에 0이 아닌 바이트 {wc['nonzero']}개 — 감시를 멈춥니다"
        if verdict != "stop" and any(l.startswith("POKE-FAULT") and "kind=i2c" in l for l in b.get("lines", [])):
            verdict, why = "stop", "런타임이 버스 전송을 거부했습니다(몸이 바뀌었을 수 있음) — 감시를 멈춥니다"
        if verdict != "ok":                                   # a body change stops the watch (re-arm on the new body)
            try:
                import bodywatch
                bv = bodywatch.after_failure(b, {"path": w["img"]})
                if isinstance(bv, dict) and bv.get("kind") in ("moved", "gone", "missing", "new"):
                    verdict, why = "stop", f"몸이 바뀌었습니다 — {bv.get('text', '')} 감시를 멈춥니다(다시 무장하세요)"
            except Exception:
                pass
        hit = verdict == "ok" and _hit(b.get("result"))
        if verdict == "ok" and "hit" not in (b.get("result") or {}):
            verdict, why = "stop", "점검 op가 hit(조건 충족 여부)를 보고하지 않습니다 — 감시를 멈춥니다"
        with _locked("w-" + w["id"]):
            w = load(w["id"]) or w
            w["bad"] = bad
            w["last"] = {"at": time.strftime("%H:%M:%S"), "verdict": verdict, "hit": hit, "lines": KO.result_lines(b.get("result")),
                         "why": why, "wipe_nonzero": wc.get("nonzero") if wc else None}
            if verdict == "stop" and w["state"] in ("armed", "resting"):
                w["state"], w["stop_reason"] = "stopped", why
            elif hit and not w.get("repeat"):
                w["state"], w["stop_reason"] = "done", "조건이 맞아 알렸습니다 (한 번 알리고 끝)"
            _save(w)
        if verdict == "stop":
            out.append(event("stopped", w, why))
        elif verdict == "strike":
            out.append(event("strike", w, why))
        if hit:
            msg = f"{w.get('summary_ko') or w['sentence']} — 조건이 맞았습니다: " + " · ".join(w["last"]["lines"])
            out.append(event("hit", w, msg))
            if notify:
                _notify(msg)
            if w.get("then"):
                out.append(event("pending_action", w, f"할 일이 기다립니다: “{w['then']}” — 사람이 확인해야 실행됩니다", action=w["then"]))
    for e in out:
        log(f"[감시] {e['text']}")
    return out

def run_forever(bench=None, stop_event=None, poll_s=2.0, log=print):
    """The hub's watch loop (CLI `say.py watch run`, or a thread in the demo server)."""
    while not (stop_event and stop_event.is_set()):
        try:
            tick(bench=bench() if callable(bench) else bench, log=log)
        except Exception as e:
            log(f"[감시] 한 번 돌리다 문제가 생겼습니다: {type(e).__name__}: {e}")
        if stop_event:
            stop_event.wait(poll_s)
        else:
            time.sleep(poll_s)
