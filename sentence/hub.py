"""hub — one sentence through the hub, as a stream of stage events (the CLI and the demo screen share it).

    for ev in hub.pipeline("LED 3번 켜줘", yes=True): print(ev)

Events: {"stage": "sentence" | "op" | "confirm" | "check" | "boot" | "result" | "empty" | "schedule" | "stop", ...}
A "stop" event ends the run without (or before) a boot: refusals (need, cannot, range, deny, unconfirmed,
declined, too_long) and errors, each with Korean "text" and "hint".

Order: record lookup (no model) → op written if new (Claude Code) → manifest policy → confirmation for
writes → image check → boot → result in Korean → trace saved in the record → the node is empty.
"""
import json, os, re, time
import manifest as MF
import record as REC
import messages_ko as KO

DEMO = {"on": False}   # decision 8: during the demo — reads and LEDs only, no 'always', no arming, no learning
UTC_OFFSET_MIN = int(os.environ.get("POKE_UTC_OFFSET_MIN", "540"))   # KST
HUB_CONSTS = {"POKE_UTC_OFFSET_MIN": UTC_OFFSET_MIN}
REPLY = re.compile(r"^\s*(NEED|CANNOT|LONG|WATCH)\s*:\s*(.+?)\s*$", re.S)

# ── used by say.make_op while the model writes ─────────────────────────────
def reply_kind(text):
    """A model answer that is not a C file: NEED / CANNOT / LONG → (kind, payload) or None."""
    m = REPLY.match(text.strip()) if text and "op_main" not in text else None
    if not m:
        return None
    kind, body = m.group(1).lower(), m.group(2).strip()
    if kind in ("long", "watch"):
        try:
            body = json.loads(body)
        except ValueError:
            return ("cannot", "한 번의 부팅(20초)보다 긴 요청인데 나눌 방법을 정하지 못했습니다" if kind == "long"
                    else "계속 지켜보는 요청인데 무엇을 볼지 정하지 못했습니다")
    return (kind, body)

def op_problems(src, sentence):
    """Manifest and parameter problems in a generated op (English, fed back to the model)."""
    try:
        m = MF.parse(src)
    except MF.ManifestError as e:
        return [str(e)]
    if m is None:
        return ["the file must start with a /* MANIFEST {...} */ block"]
    p = MF.validate(m) + MF.cross_check(m, src)
    params = REC.parse_params(src)
    if re.search(r"/\*\s*PARAMS", src) is None:
        p.append("the file must have a /* PARAMS [...] */ block (PARAMS [] when the sentence has no digit numbers)")
    _, pp = REC.check_params(params, src, sentence)
    return p + pp

def hub_consts():
    """Time zone, and where each part is wired on this body: POKE_ADDR_<FRAGMENT> (say.wired_addrs)."""
    import say
    out = dict(HUB_CONSTS)
    try:
        for name, e in say.catalog().items():
            out[f"POKE_ADDR_{name.upper()}"] = int(str(say.wired_addrs(name, e["default_addr"])[0]), 0)
    except Exception:
        pass
    return out

def with_hub_consts(src):
    """Hub-owned constants — rewritten like parameters, added when the op uses one undefined."""
    consts = hub_consts()
    src = REC.apply_params(src, {k: v for k, v in consts.items() if REC.define_value(src, k) is not None})
    for k, v in consts.items():
        if re.search(rf"\b{k}\b", src) and REC.define_value(src, k) is None:
            src = f"#define {k} {v}\n" + src
    return src

# ── the pipeline ───────────────────────────────────────────────────────────
def zero_fill_in(img_path):
    """Does this image's runtime zero the boot's memory before the reset (rt_power.c hook)? Read from its symbols."""
    import subprocess
    elf = (img_path or "").rsplit(".", 1)[0] + ".elf"
    try:
        out = subprocess.run(["aarch64-elf-nm", elf], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(re.search(r"\brtp_power_off\b", out)) and " T rtp_power_off" in out

def wipe_region(img_path):
    """The memory this image's boot used beyond its own code — op pages, runtime data, stack —
    [__rt_text_end, __rt_end), from the image's symbols (the same range say.wipe_check reads back).
    → {"from", "to", "bytes"} or None."""
    import subprocess
    elf = (img_path or "").rsplit(".", 1)[0] + ".elf"
    try:
        out = subprocess.run(["aarch64-elf-nm", elf], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    sym = {m.group(2): int(m.group(1), 16) for m in re.finditer(r"^([0-9a-f]+)\s+\S\s+(__rt_text_end|__rt_end)$", out, re.M)}
    if len(sym) < 2 or sym["__rt_end"] <= sym["__rt_text_end"]:
        return None
    return {"from": sym["__rt_text_end"], "to": sym["__rt_end"], "bytes": sym["__rt_end"] - sym["__rt_text_end"]}

def _kb(n):
    return f"{n / 1024:.0f} KB" if n >= 1024 else f"{n} B"

def empty_text(powered_off, zeroed, wc=None, region=None):
    """What the person is told about the node after a boot — only what is true, measured where we can."""
    if not powered_off:
        return "스스로 꺼지지 않아 허브가 강제로 껐습니다"
    if wc:
        if wc.get("nonzero", 1) == 0:
            return f"비움 — 리셋 뒤 실측: 이 부팅이 쓴 메모리 {_kb(wc['bytes'])} 중 0이 아닌 바이트 0, 부트로더만 대기"
        return f"⚠ 비움 실패 — 리셋 뒤 실측: {_kb(wc['bytes'])} 중 {wc['nonzero']} 바이트가 0이 아님"
    if zeroed:
        size = f" {_kb(region['bytes'])}" if region else ""
        return f"비움 — 런타임이 리셋 전 이 부팅의 메모리{size}를 0으로 지움 (이미지에서 읽은 범위, 실측 없음), 부트로더만 대기"
    return "꺼짐 — 리셋 후 부트로더만 대기 (메모리 0 채우기 없음)"

def _body_label():
    try:
        import body
        return body.label()
    except Exception:
        return None

def _stop(kind, text, hint=""):
    return {"stage": "stop", "kind": kind, "text": text, "hint": hint}

YES = re.compile(r"^\s*(y|yes|네|예|응|ㅇ|ok|좋아|그래|실행|a|always|항상)", re.I)
ALWAYS = re.compile(r"^\s*(a|always|항상)", re.I)
NO_ANSWER = re.compile(r"^\s*(y|yes|n|no|네|예|아니|아니오|응|ㅇ|ㄴ|ok)\s*$", re.I)

PREFLIGHT_KO = {"serial": "보고선(USB-시리얼)", "network": "이더넷 주소 10.0.0.1", "tftp": "TFTP 서버", "rivals": "다른 부트로더·남은 이미지",
                "firmware": "펌웨어", "config": "config.txt", "image": "이미지", "eeprom": "부트로더 설정"}

def preflight(img_path=None):
    """netboot/preflight.py (실기기팀) → (rows, failed names)."""
    import subprocess, sys
    here = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, os.path.join(here, "netboot", "preflight.py")] + ([img_path] if img_path else []) + ["--json"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=here).stdout
        rows = json.JSONDecoder().raw_decode(out[out.index("["):])[0] if "[" in out else []   # the JSON list, then a summary
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        rows = [{"check": "preflight", "level": "FAIL", "msg": f"preflight.py did not run: {e}"}]
    return rows, [r["check"] for r in rows if r.get("level") == "FAIL"]

def pipeline(sentence, *, bench=None, script=(), yes=False, no=False, ask=None, target="twin", depth=0, retry_fault=True, preflight_first=False,
             from_watch=False):
    """ask(kind, question) → str | None  — how the person answers (None: no one to ask).
    kind "confirm": y = this time, a/항상 = remember the approval in the record, anything else = no.
    kind "need": a real answer is appended to the sentence and the whole pipeline runs again."""
    import say
    t0 = time.time()
    yield {"stage": "sentence", "text": sentence}
    yield {"stage": "op", "state": "looking"}
    if target == "twin":                                   # 실기기 H7: know the body before judging ops against it
        try:
            import bodycheck
            bodycheck.present()
        except Exception:
            pass
    try:
        op = say.make_op(sentence, log=lambda s: None)
    except KO.Refusal as r:
        yield from _reply(sentence, r, bench=bench, script=script, yes=yes, no=no, ask=ask, target=target, depth=depth); return
    except Exception as e:                                 # hub-side failure: Korean, no traceback
        text, hint = KO.explain(e)
        yield _stop("error", text, hint); yield {"stage": "error_detail", "text": f"{type(e).__name__}: {e}"}; return

    for attempt in (1, 2):
        m, rec = op.get("manifest") or {}, op.get("record") or {}
        params = op.get("params") or {}
        yield {"stage": "op", "state": "ready", "cached": op.get("cached"), "gen_s": op.get("gen_s"), "rounds": op.get("rounds"),
               "record": rec.get("id"), "params": params, "src": op.get("src"), "op_bytes": (op.get("check") or {}).get("op_bytes"),
               "image_bytes": len(op.get("img") or b""), "uses": [a["fn"] for a in MF.access(m)], "matched": op.get("matched"),
               "repronounced": op.get("repronounced"), "body": _body_label()}

        # policy and confirmation (asked once per run, even if the op is rewritten after a fault)
        summary = KO.fix_josa(MF.summary(m, params, rec.get("params")))
        verdict, why = MF.decide(m)
        if DEMO["on"] and verdict != "deny":
            other = [a["fn"] for a in MF.access(m) if a["access"] == "write" and a["fn"] != "led_set"]
            if other:
                verdict, why = "deny", ["데모 중에는 읽기와 LED만 합니다 (" + ", ".join(other) + ")"]
        if verdict == "deny":
            yield _stop("deny", "허브 정책이 이 요청을 막았습니다: " + "; ".join(why), "노드를 켜지 않았습니다."); return
        if verdict == "confirm" and attempt == 1:
            no_always = from_watch or DEMO["on"]
            how = "기억된 승인 (항상)" if rec.get("approval") and not no_always else None   # a watch's action / the demo always asks
            if not how:
                if no:
                    yield _stop("declined", "실행하지 않았습니다 (--no).", ""); return
                if yes:
                    how = "--yes"
                else:
                    a = None
                    if ask:
                        yield {"stage": "confirm", "state": "asking", "summary": summary, "why": why}
                        a = ask("confirm", summary)
                    if a is None:
                        yield _stop("unconfirmed", f"장치 상태를 바꾸는 요청이라 확인이 필요합니다: {summary}", "--yes를 붙이면 확인 없이 실행합니다."); return
                    if not YES.match(a):
                        if re.match(r"^\s*(n|no|아니|아니오|ㄴ|싫어|취소)", a, re.I):
                            yield _stop("declined", "실행하지 않았습니다.", ""); return
                        yield _stop("declined", f"확인 질문에 대한 답이 아니라서 실행하지 않았습니다 (받은 답: '{a}').",
                                    "y = 이번만, a = 항상, n = 안 함 으로 답하세요."); return
                    how = "항상 (기억함)" if ALWAYS.match(a) else "이번만"
                    if ALWAYS.match(a) and no_always:
                        how = "이번만 (감시가 부른 행동은 '항상'을 저장하지 않음)"
                    elif ALWAYS.match(a) and rec.get("id"):
                        if not (from_watch or DEMO["on"]): REC.approve(rec, "사람: 항상")   # never stored for a watch's action
            yield {"stage": "confirm", "state": "ok", "summary": summary, "how": how}
        elif attempt == 1:
            yield {"stage": "confirm", "state": "not_needed", "summary": summary}

        # image check — a refused image never boots (cache path too)
        c = op.get("check") or {}
        yield {"stage": "check", "ok": bool(c.get("ok")), "problems": c.get("problems", [])[:6], "op_bytes": c.get("op_bytes"),
               "warnings": c.get("warnings", [])[:4]}
        if not c.get("ok"):
            yield _stop("refused", "이미지 검사가 부팅을 막았습니다.", "; ".join(c.get("problems", [])[:3])); return

        # a real Pi: the hub's own checks before the first netboot (preflight)
        if target == "pi" and preflight_first and attempt == 1:
            rows, failed = preflight(op.get("path"))
            yield {"stage": "preflight", "rows": rows, "failed": failed}
            if failed:
                names = ", ".join(dict.fromkeys(PREFLIGHT_KO.get(f, f) for f in failed))
                yield _stop("preflight", f"실기기 준비가 끝나지 않아 부팅하지 않았습니다: {names}", "자세한 내용은 위 점검 줄, 설정은 netboot/README.md"); return

        # boot
        yield {"stage": "boot", "state": "on", "target": target}
        try:
            if target == "pi":
                b = say.boot_pi(op["path"])
            elif target == "netboot-twin":
                b = say.boot_netboot_twin(op["path"])
            else:
                state = {**say.default_bench(), **(bench or {})}         # the bench's defaults, then what the caller set
                b = say.boot(op["path"], state, script=script)          # any bench: ext truth, wipe_check and effects included
        except Exception as e:
            text, hint = KO.explain(e)
            yield _stop("error", text, hint); yield {"stage": "error_detail", "text": f"{type(e).__name__}: {e}"}; return
        say_lines = [l[len("POKE-SAY "):] for l in b.get("lines", []) if l.startswith("POKE-SAY ")]
        yield {"stage": "boot", "state": "off" if b.get("powered_off") else "forced", "node_ms": b.get("node_ms"),
               "boot_to_off_ms": b.get("boot_to_off_ms"), "say": say_lines,
               "fault": next((l for l in b.get("lines", []) if l.startswith("POKE-FAULT")), None)}
        fb = say.fault_feedback(b)                          # verify team: one new op if the node stopped this one
        if not fb or attempt == 2 or not retry_fault:
            break
        safe, why_ko = say.retry_safe(b, op["path"])       # verify team X2: never repeat a change to the room unasked
        if not safe:
            a = None
            if ask:
                yield {"stage": "confirm", "state": "asking", "summary": f"{why_ko}. 새 op로 다시 할까요?", "why": [why_ko]}
                a = ask("confirm", f"{why_ko}. 새 op로 다시 할까요?")
            if not a or not YES.match(a):
                break                                      # report this run as it ended
        yield {"stage": "op", "state": "retry", "why": fb}
        try:
            op = say.make_op(sentence, log=lambda s: None, feedback=fb)
        except Exception as e:
            text, hint = KO.explain(e)
            yield _stop("error", text, hint); return

    try:
        fx = say.effects_report(b, op.get("path"))
    except Exception:
        fx = {"ok": None}
    problem = KO.boot_problem(b)
    tc = None
    if rec.get("id"):
        import trace_check as TC, hashlib as _h
        fxs = TC.fx_summary(b, op.get("path"))
        bkey = _h.sha1(json.dumps(bench or {}, sort_keys=True).encode()).hexdigest()[:8] if target == "twin" else None
        if op.get("repronounced"):                         # decision 6: hold the new pronunciation against the card
            try:
                import body as BD
                cur = REC.load(rec["id"]) or rec
                prev = (cur.get("lineage") or [{}])[-1]
                ref_w = [a["fn"] for a in MF.access(prev.get("manifest") or {}) if a["access"] == "write"] if prev.get("manifest") else None
                ref_r = sorted({a["frag"] for a in MF.access(prev.get("manifest") or {})}) if prev.get("manifest") else None
                tc = TC.compare(cur, b.get("result"), fxs, BD.fp(), bkey, ref_writes=ref_w, ref_reads=ref_r)
            except Exception as e:
                tc = {"verdict": "unknown", "reasons": [f"대조하지 못했습니다 ({type(e).__name__})"], "ref": 0}
        try: REC.trace(rec, sentence, params, {**b, "fx_summary": fxs, "bench_key": bkey})
        except OSError: pass
        if tc:
            try:
                _, adopted = REC.judge(rec, tc, adopt=tc["verdict"] != "mismatch")
                tc["adopted"] = adopted
            except OSError:
                pass
            yield {"stage": "trace_check", **tc, "text": TC.VERDICT_KO.get(tc["verdict"], tc["verdict"])}
    yield {"stage": "result", "ok": problem is None, "result": b.get("result"), "lines": KO.result_lines(b.get("result"), m.get("labels_ko")),
           "problem": problem, "truth": b.get("truth"), "summary": summary,
           "effects": KO.effects_lines(fx), "effects_ok": fx.get("ok")}
    zeroed = zero_fill_in(op.get("path"))
    region, wc = (wipe_region(op.get("path")) if zeroed else None), b.get("wipe_check")
    yield {"stage": "empty", "powered_off": bool(b.get("powered_off")), "zeroed": zeroed, "vm_status": b.get("vm_status"),
           "text": empty_text(bool(b.get("powered_off")), zeroed, wc, region), "total_s": round(time.time() - t0, 2),
           "wipe": region, "wipe_check": wc, "wipe_ok": (wc.get("nonzero") == 0) if wc else None}
    yield {"stage": "done", "boot": {k: v for k, v in b.items() if k != "lines"},
           "op": {k: v for k, v in op.items() if k not in ("img", "src", "record")}}
    # the body may have changed under us (a part moved, gone, or new): 실기기팀's bodywatch decides, the hub
    # tells the person and, when the wiring was corrected, says the sentence once more (re-pronounced if needed)
    if problem and target == "twin" and depth < 2:
        verdict = _bodywatch(b, op)
        if verdict:
            yield {"stage": "body", **verdict}
            if verdict.get("rerun"):
                yield from pipeline(sentence, bench=bench, script=script, yes=yes, no=no, ask=ask, target=target,
                                    depth=depth + 1, retry_fault=retry_fault)
            elif verdict.get("kind") == "gone":                # a part left the body: say so and stop, no retry
                yield _stop("body_gone", verdict.get("text", ""), "다시 연결한 뒤 같은 문장을 말해 주세요.")
    elif not problem and target == "twin" and depth == 0:
        hint = _bodyhint()                                     # a part nobody knows yet (scan at most every 10 min per body)
        if hint:
            yield {"stage": "body", **hint}

def _bodyhint():
    """bodywatch.after_success() → a 'new part' notice or None. Never raises."""
    try:
        import bodywatch
        return bodywatch.after_success()
    except Exception:
        return None

def _bodywatch(b, op):
    """sentence/bodywatch.py (실기기팀), if present: after_failure(boot, op) → None or
    {"kind": "moved" | "gone" | "new" | "unclear", "text": Korean, "rerun": bool, "wiring": {...}}. Never raises."""
    try:
        import bodywatch
    except ImportError:
        return None
    try:
        v = bodywatch.after_failure(b, {k: op.get(k) for k in ("path", "manifest", "src", "key")})
        return v if isinstance(v, dict) and v.get("text") else None
    except Exception as e:
        return {"kind": "error", "text": f"몸 변화를 확인하지 못했습니다 ({type(e).__name__})", "rerun": False}

def _reply(sentence, r, *, bench, script, yes, no, ask, target, depth):
    """make_op answered with something other than an op."""
    if r.kind == "gone":
        yield _stop("body_gone", r.text, getattr(r, "hint", "")); return
    if r.kind == "deny":
        yield _stop("deny", r.text, "노드를 켜지 않았습니다."); return
    if r.kind == "range":
        yield _stop("range", r.text, "노드를 켜지 않았습니다."); return
    if r.kind == "cannot":
        yield {"stage": "op", "state": "reply", "kind": "cannot"}
        yield _stop("cannot", f"이 노드로는 할 수 없습니다: {r.text}", "노드를 켜지 않았습니다."); return
    if r.kind == "watch":
        yield from _arm_watch(sentence, r.text, yes=yes, no=no, ask=ask, bench=bench); return
    if r.kind == "long":
        import schedule
        yield {"stage": "op", "state": "reply", "kind": "long"}
        yield from schedule.run(sentence, r.text, bench=bench, yes=yes, no=no, ask=ask, target=target); return
    if r.kind == "need":
        q = r.text if r.text.rstrip().endswith("?") else r.text.rstrip() + "?"
        yield {"stage": "op", "state": "reply", "kind": "need", "question": q}
        a = ask("need", q) if ask and depth < 2 else None
        if not a or NO_ANSWER.match(a):
            yield _stop("need", f"되물음: {q}", "답을 붙여 다시 말하거나 --answer \"…\"로 답하세요."); return
        yield {"stage": "answer", "text": a}
        yield from pipeline(f"{sentence} — {a}", bench=bench, script=script, yes=yes, no=no, ask=ask, target=target, depth=depth + 1)
        return
    yield _stop(r.kind, r.text, getattr(r, "hint", ""))


def _arm_watch(sentence, spec, *, yes, no, ask, bench=None):
    """A standing intent (decision 1): prepare the read-only check, show the caps, ask, arm, run the first check."""
    import say, watch
    yield {"stage": "op", "state": "reply", "kind": "watch"}
    draft, prob = watch.prepare(sentence, spec)
    if prob:
        yield _stop("watch_refused", f"이 요청은 상시 감시로 돌릴 수 없습니다: {prob}", "감시는 읽기만 하는 op로만 무장합니다."); return
    caps = say.WATCH
    text = (f"“{draft['check']}”를 {draft['every_s']:g}초마다 부팅해 확인합니다 — 읽기만, 노드는 그 사이 꺼져 있음. "
            f"조건이 맞으면 알리고 " + ("계속 지켜봅니다" if draft.get("repeat") else "끝냅니다") +
            (f"; 할 일 “{draft['then']}”은 그때 따로 묻습니다" if draft.get("then") else "") +
            f". 하루 {caps['day_cap']}회·{caps['ttl_h']}시간 뒤 만료, 두 번 연속 실패하면 멈춤.")
    yield {"stage": "watch", "state": "plan", "text": text, "draft": {k: draft[k] for k in ("id", "check", "every_s", "then", "summary_ko", "check_summary")}}
    if DEMO["on"]:
        yield _stop("demo", "데모 중이라 지켜보기는 보여만 주고 시작하지 않습니다.", "데모가 끝난 뒤 같은 문장을 말하면 시작할 수 있습니다."); return
    if no:
        yield _stop("declined", "지켜보기를 시작하지 않았습니다 (--no).", ""); return
    if not yes:
        a = ask("confirm", "지켜보기를 시작할까요? " + text) if ask else None
        if a is None:
            yield _stop("unconfirmed", "지켜보기는 사람이 시작을 확인해야 합니다.", "--yes를 붙이거나 화면에서 시작하세요."); return
        if not YES.match(a):
            yield _stop("declined", "지켜보기를 시작하지 않았습니다.", ""); return
    w, prob = watch.arm(draft)
    if prob:
        yield _stop("watch_refused", prob, ""); return
    yield {"stage": "watch", "state": "armed", "id": w["id"], "text": f"지켜보는 중 ({w['id']}). 데모 화면이 떠 있거나 `say.py watch run`이 돌 때 확인합니다."}
    evs = watch.tick(bench=bench, log=lambda *a: None)     # the first check now, so the person sees it work
    w = watch.load(w["id"]) or w
    yield {"stage": "watch", "state": "first", "id": w["id"], "last": w.get("last"), "events": evs, "watch": watch.describe(w)}
