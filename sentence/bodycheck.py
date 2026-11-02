"""bodycheck — when the body changes, the hub notices and answers for it (실기기팀, B1).

A sentence boot that finds a part silent (a transfer the runtime recorded as "no ACK", POKE-EFFECTS) or
is stopped for the bus (POKE-FAULT kind=i2c) makes the hub look at the body again: a read-only bus scan
(probe.scan, 14 power-ons, ~1 s) compared with where the hub believes each part is. Three answers:

  moved   a part the hub knows is silent at its address, and a new address answers in the same address
          family (the part's address pins: addr & 0x78) with the part's register signature
          → wiring.json updated (say.WIRING, the verify team's X3 path) and the sentence runs again
  gone    the part is silent and nothing on the bus looks like it → said plainly, nothing is retried
  new     an address answers that no fragment knows → an incubation is suggested, never started (it costs
          minutes and model calls): `incubation.py run 0xNN`

    after_boot(b, op)       → None, or {"kind": "moved"|"gone"|"new", "text", "hint", "retry", "wiring", "scan", ...}
    survey()                → the comparison itself (no boot result needed)
    learn_signatures()      → record each known part's register signature on the body as it is now
                              (incubated/signatures.json; done once per body the hub trusts)

Signatures: for each known part, registers 0–7 (2 bytes each, with a register pointer), read on two
power-ons with different bench values; the bytes that did not change are the signature. Register
reads on a candidate address are made only inside the vanished part's address family — an unknown
part elsewhere is only ever scanned (read, no pointer, no write).
"""
import os, json, time
HERE = os.path.dirname(os.path.abspath(__file__))
SIGS = os.path.join(os.environ.get("POKE_INCUBATED") or os.path.join(HERE, "incubated"), "signatures.json")
LAST = None

def _say():
    import say
    return say

def known_parts(all_benches=False):
    """{frag: (addresses where the hub believes it is, Korean name)} for the fragments of this body."""
    say = _say()
    import manifest as MF
    cat = json.load(open(os.path.join(say.FRAGS, "catalog.json"))) if all_benches else say.catalog()
    dev = {}
    for fn, e in MF.FN.items():
        dev.setdefault(e["frag"], e.get("dev", e["frag"]))
    out = {}
    for frag, e in cat.items():
        try: d = int(str(e.get("default_addr")), 16)
        except (TypeError, ValueError): continue
        out[frag] = ([int(str(a), 0) for a in say.wired_addrs(frag, d)], dev.get(frag, frag))
    return out

def family(a):
    return a & 0x78

# ── signatures ──────────────────────────────────────────────────────────────
def _dump(addrs, state=None):
    import probe
    out = {}
    for i in range(0, len(addrs), 2):                       # 2 parts × 8 registers = 16 transfers per power-on
        grp = addrs[i:i + 2]
        steps = [("r", a, r, 2) for a in grp for r in range(8)]
        rec = probe.run(steps, state=state)
        for (op, a, r, n), o in zip(steps, rec["out"]):
            out.setdefault(a, {})[r] = None if o.startswith("err") or o.startswith("(") else o
    return out

def learn_signatures():
    import bench as BN
    parts = {f: v for f, v in known_parts().items()}
    addrs = sorted({a for f, (al, _) in parts.items() for a in al})
    s1 = BN.default_state()
    s2 = {k: (v + 4321 if isinstance(v, int) else ("low" if v == "high" else "high")) for k, v in s1.items()}
    d1, d2 = _dump(addrs, s1), _dump(addrs, s2)
    say = _say()
    lock = say.key_lock("body-signatures"); lock.__enter__()
    sigs = json.load(open(SIGS)) if os.path.exists(SIGS) else {}
    for f, (al, _) in parts.items():
        a = al[0]
        regs = {str(r): d1.get(a, {}).get(r) for r in range(8) if d1.get(a, {}).get(r) is not None and d1[a][r] == d2.get(a, {}).get(r)
                and d1[a][r] not in ("00 00", "ff ff")}        # all-zero / all-one reads tell nothing apart
        sigs.pop(f, None)
        if len(regs) >= 2:
            sigs[f] = {"addr": f"0x{a:02x}", "regs": regs, "learned": time.strftime("%Y-%m-%d %H:%M"), "bench": BN.name()}
    os.makedirs(os.path.dirname(SIGS), exist_ok=True)
    try: say.write_json_atomic(SIGS, sigs, indent=1)
    finally: lock.__exit__(None, None, None)
    return sigs

def identify_at(frag, addr):
    """H9: the part's own <frag>_identify(addr) (learned with it; read-only) in one power-on → True/False, or None
    when the fragment has none or it cannot tell."""
    say = _say()
    cat = json.load(open(os.path.join(say.FRAGS, "catalog.json")))
    fn = (cat.get(frag) or {}).get("identify")
    if not fn:
        return None
    import bench as BN
    src = f'#include "rt.h"\n#include "{cat[frag]["header"]}"\nvoid op_main(void) {{ report_int("id", {fn}({addr:#x})); }}\n'
    allow = {"bus": "i2c1", "devices": [{"addr": addr, "read": True, "pointer": True, "write_regs": [], "max_write": 1}]}
    _, _, img = say.build(src, f"body-id-{frag}-{addr:02x}", allow=allow)
    v = (BN.boot(img).get("result") or {}).get("id")
    return True if v == 1 else (False if v == 0 else None)

def matches(frag, addr):
    """Is the part at addr this known part? Its identify() when it has one (H9), else its register signature
    (register reads — call only inside frag's address family)."""
    who = identify_at(frag, addr)
    if who is not None:
        return who
    sigs = json.load(open(SIGS)) if os.path.exists(SIGS) else {}
    sig = sigs.get(frag, {}).get("regs")
    if not sig or len(sig) < 2:
        return None                                         # no signature: cannot tell
    got = _dump([addr]).get(addr, {})
    return all(got.get(int(r)) == v for r, v in sig.items())

# ── what answers on this body (H7: the catalog follows it) ──────────────────
def body_key():
    """The body as the hub can name it without asking it: the twin's parts and where the hub believes they are
    wired. (Not body.fp — that includes the catalog, which is computed from this.)"""
    import hashlib
    say = _say()
    core = {"parts": sorted([m, a] for m, _, a in say.BENCH), "wiring": say.wiring()}
    return hashlib.sha1(json.dumps(core, sort_keys=True).encode()).hexdigest()[:10]

def _present_file(key):
    return os.path.join(_say().BUILD, "body", f"present-{key}.json")

def remember_present(addrs, gone=(), key=None, boots=None, moved=None, new=None):
    say = _say()
    key = key or body_key()
    f = _present_file(key); os.makedirs(os.path.dirname(f), exist_ok=True)
    say.write_json_atomic(f, {"present": sorted(set(addrs)), "gone": sorted(set(gone)), "t": time.time(),
                              "when": time.strftime("%Y-%m-%d %H:%M:%S"), "power_ons": boots,
                              "moved": {k: f"0x{v:02x}" for k, v in (moved or {}).items()},
                              "new": [f"0x{a:02x}" for a in (new or [])]})

def forget_body():
    """Drop what the hub remembers about this body's parts (the next catalog() scans it again)."""
    try: os.remove(_present_file(body_key()))
    except OSError: pass

def present(force=False):
    """{"present": [addr…], "gone": [frag…]} for this body — from the last scan of it (cached per body key; a
    first look costs one read-only scan, ~1 s). "gone" = parts a body check found missing since that scan."""
    say = _say()
    key = body_key(); f = _present_file(key)
    if not force:
        try: return json.load(open(f))
        except (OSError, ValueError): pass
    with say.key_lock(f"present-{key}"):
        if not force:
            try: return json.load(open(f))
            except (OSError, ValueError): pass
        import probe
        addrs, boots = probe.scan()
        remember_present(addrs, (), key, boots)
    # a body seen for the first time is also checked at once: a known part found at another address in its
    # family is re-wired (so the catalog lists it where it is), a missing one is marked gone
    try:
        s = judge(addrs, boots)
        if s["moved"]:
            set_wiring(s["moved"])
            remember_present(addrs, s["gone"], moved=s["moved"], new=s["new"])   # under the re-wired body's key
    except Exception:
        pass
    return json.load(open(_present_file(body_key())))

# ── the comparison ──────────────────────────────────────────────────────────
def survey():
    t0 = time.time()
    now = present(force=True)
    s = judge(now["present"], now.get("power_ons"), already=True)
    s["seconds"] = round(time.time() - t0, 2)
    return s

def judge(here, boots=None, already=False):
    """Compare what answers (here) with where the hub believes each known part is → moved / gone / new."""
    t0 = time.time()
    import bench as BN
    say = _say()
    raw = json.load(open(os.path.join(say.FRAGS, "catalog.json")))
    everywhere = known_parts(all_benches=True)
    # expected on this body: the original parts, and parts learned on this bench; any learned part may turn up
    # moved (identified where it answers), but only an expected one is ever called gone
    expected = {f for f in everywhere if not (raw[f].get("incubated") or raw[f].get("bench")) or BN.name() in (raw[f].get("bench") or [])}
    parts = {f: v for f, v in everywhere.items() if f in expected}
    known_addrs = {a for f, (al, _) in everywhere.items() for a in al}
    missing = {f: v for f, v in everywhere.items() if not any(a in here for a in v[0])}
    extra = [a for a in here if a not in known_addrs]
    moved, gone = {}, []
    for f, (al, name) in sorted(missing.items(), key=lambda kv: kv[0] not in expected):
        cands = [a for a in extra if any(family(a) == family(o) for o in al) and a not in moved.values()]
        hit = next((a for a in cands if matches(f, a)), None)
        if hit is not None:
            moved[f] = hit
        elif f in expected:
            gone.append(f)
    parts.update({f: everywhere[f] for f in moved})
    new = [a for a in extra if a not in moved.values()]
    remember_present(here, gone, boots=boots, moved=moved, new=new)   # the catalog drops a gone part at once
    return {"present": here, "power_ons": boots, "seconds": round(time.time() - t0, 2), "moved": moved,
            "gone": gone, "new": new, "names": {f: v[1] for f, v in parts.items()},
            "was": {f: v[0] for f, v in parts.items()}}

def silent_parts(b):
    """Addresses the node found silent (no ACK) or was stopped on (kind=i2c) in this boot."""
    say = _say()
    fx = say.parse_effects(b.get("lines", [])) or {}
    nack = {x[2] for x in fx.get("x", []) if len(x) >= 6 and x[5] == 2}
    fault = next((l for l in b.get("lines", []) if l.startswith("POKE-FAULT") and "kind=i2c" in l), None)
    if fault:
        for tok in fault.split():
            if tok.startswith("addr="):
                try: nack.add(int(tok[5:], 16))
                except ValueError: pass
    return sorted(nack), fault

def set_wiring(moves):
    """frag → new address, merged into the wiring file the hub uses (say.WIRING) — one writer at a time, whole-file replace."""
    say = _say()
    with say.key_lock("wiring"):
        try: w = json.load(open(say.WIRING))
        except (OSError, ValueError): w = {}
        tbl = w.get("wiring", w) if isinstance(w, dict) else {}
        for f, a in moves.items():
            tbl[f] = [f"0x{a:02x}"]
        if isinstance(w, dict) and "wiring" in w: w["wiring"] = tbl
        else: w = tbl
        say.write_json_atomic(say.WIRING, w, indent=1)

def explain(s):
    """Korean text for a survey."""
    lines, hints = [], []
    for f, a in s["moved"].items():
        lines.append(f"{s['names'].get(f, f)}({f.upper()})가 0x{s['was'][f][0]:02x}에서 0x{a:02x}로 옮겨졌습니다")
    for f in s["gone"]:
        lines.append(f"{s['names'].get(f, f)}({f.upper()})가 몸에서 빠졌습니다 — 0x{s['was'][f][0]:02x}에서 답이 없고, 버스 다른 곳에도 없습니다")
        hints.append("다시 연결한 뒤 같은 문장을 말해 주세요")
    for a in s["new"]:
        lines.append(f"처음 보는 장치가 0x{a:02x}에 있습니다")
        hints.append(f"배우게 하려면: ../sim/body/.venv/bin/python incubation.py run 0x{a:02x} (Claude Code가 몇 분 동안 식별·조각·검증)")
    return lines, hints

def after_boot(b, op=None):
    """A boot that met a silent part → look at the body. None when the boot met none."""
    silent, fault = silent_parts(b)
    if not silent:
        return None
    s = survey()
    lines, hints = explain(s)
    if s["moved"]:
        set_wiring(s["moved"])
        remember_present(s["present"], s["gone"])           # the same scan names the re-wired body (a new key)
    kind = "gone" if s["gone"] else ("moved" if s["moved"] else ("new" if s["new"] else "unclear"))
    if kind == "unclear":
        lines = [f"0x{silent[0]:02x}의 장치가 답하지 않았지만, 지금 버스에서는 알던 장치가 모두 제자리에 있습니다 (일시적일 수 있음)"]
    return {"kind": kind, "silent": silent, "fault": fault, "text": "; ".join(lines), "hint": " · ".join(hints),
            "retry": kind == "moved", "wiring": {f: f"0x{a:02x}" for f, a in s["moved"].items()}, "scan": s}

def survey_hint():
    """For a boot that went fine: only new parts are worth a word (the hub cannot use them yet)."""
    s = survey()
    if not s["new"]:
        return None
    lines, hints = explain({**s, "moved": {}, "gone": []})
    return {"kind": "new", "text": "; ".join(lines), "hint": " · ".join(hints), "retry": False, "scan": s}

def maybe_hint(max_age_s=600):
    """After a boot that went fine: survey at most every max_age_s per body (a scan costs ~1 s), and speak
    only of parts nobody knows. The time of the last survey per body: build/body/survey.json."""
    say = _say()
    import body as BD
    f = os.path.join(say.BUILD, "body", "survey.json")
    key = BD.fp()
    try: seen = json.load(open(f))
    except (OSError, ValueError): seen = {}
    if time.time() - seen.get(key, 0) < max_age_s:
        return None
    h = survey_hint()
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with say.key_lock("body-seen"):                          # merge with what other processes wrote meanwhile
        try: seen = json.load(open(f))
        except (OSError, ValueError): seen = {}
        seen[key] = time.time()
        say.write_json_atomic(f, seen)
    return h

def run_sentence(sentence, log=print, hint_age_s=600):
    """The flow the hub follows (reference for hub.pipeline): op → boot → if a part was silent, look at the
    body: moved → wiring fixed and one more run; gone → said plainly; new → suggested. A run that went fine
    still mentions a part nobody knows (at most once per body every hint_age_s)."""
    say = _say()
    import bench as BN
    out = {"sentence": sentence, "runs": []}
    import messages_ko as KO
    # look at the body before anything is written for it: a first look re-wires a moved part and marks a
    # missing one gone, so the op is pronounced for the body as it is
    first = present()
    out["first_look"] = {k: first.get(k) for k in ("moved", "gone", "new")}
    if first.get("moved"):
        log("[몸] 처음 본 몸: " + ", ".join(f"{f} → {a}" for f, a in first["moved"].items()) + " (배선 갱신)")
    for attempt in (1, 2):
        try:
            op = say.make_op(sentence, log=lambda *a: None)
        except KO.Refusal as r:                               # e.g. the hub already knows the part is gone
            body_text = None
            if first.get("gone"):
                names = known_parts(all_benches=True)
                body_text = "; ".join(f"{names.get(g, ('', g))[1]}({g.upper()})가 몸에서 빠졌습니다" for g in first["gone"])
            out["runs"].append({"refused": r.kind, "text": str(r), "body": {"kind": "gone", "text": body_text} if body_text else None})
            out["end"] = f"refused:{r.kind}"
            return out
        b = BN.boot(op["path"])
        run = {"result": b.get("result"), "powered_off": b.get("powered_off"), "truth": b.get("truth"),
               "repronounced": bool(op.get("repronounced")), "cached": op.get("cached")}
        out["runs"].append(run)
        v = after_boot(b, op)
        run["body"] = v
        if v:
            log(f"[몸] {v['text']}" + (f" — {v['hint']}" if v.get("hint") else ""))
        if v and v["retry"] and attempt == 1:
            log("[몸] 배선을 고쳤습니다: " + ", ".join(f"{f} → {a}" for f, a in v["wiring"].items()) + " — 다시 실행합니다")
            continue
        if not v:
            h = maybe_hint(hint_age_s)
            run["body"] = h
            if h: log(f"[몸] {h['text']} — {h['hint']}")
        out["end"] = (v or {}).get("kind") or ((run["body"] or {}).get("kind") if run["body"] else "ok")
        return out
    out["end"] = "moved-again"
    return out
