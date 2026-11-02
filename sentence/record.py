"""record — the hub's memory of sentences (explicit memory, not a build cache).

One record per thing the hub has learned to do, in sentence/build/records/<id>.json:

    {"id", "kind": "op" | "need" | "cannot" | "long",
     "sentences": [every wording heard that mapped here],
     "template":  normalized sentence with numbers as '#',
     "params":    [{"name": "P_LED", "num": 0, "scale": 1, "min": 0, "max": 15, "ko": "LED 번호"}],
     "templated": true when every number in the sentence is a parameter (only then may other numbers reuse it),
     "op": C source (with #define P_x <value> lines), "manifest": {...},     # kind op
     "text": "어느 LED를 켤까요?",                                           # kind need / cannot (long: the plan)
     "colophon": {"model", "date", "gen_s", "rounds", "catalog"},
     "approval": null | {"at", "how"},          # set only when the person said "always" (항상)
     "traces": [{"at", "sentence", "params", "result", "ok", "node_ms"}]}   # last 20 results

Lookup, in order, never calling a model:
  1. the normalized sentence was heard before            → that record (its own numbers as parameters)
  2. same template, record is templated, values in range → that record with the new values
     values out of range                                  → a "range" answer (no boot, no model)
  3. nothing                                              → None: the hub writes a new op

Normalization: NFKC, lower case, whitespace and punctuation removed ("LED 4번 켜 줘?" == "led4번켜줘").
Parameters are compile-time constants: the hub rewrites `#define P_x v` and rebuilds (0.1 s).
"""
import json, os, re, time, hashlib, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
RECORDS = os.environ.get("POKE_RECORDS", os.path.join(HERE, "build", "records"))   # hub memory (user data, not code)
NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?|(?<=[\w.])\d+(?:\.\d+)?")   # "LED -1번" keeps its minus; "2026-10" does not
PUNCT = re.compile(r"[\s\?\!\.,~·…'\"“”‘’:;()\[\]{}\-]+")
MAX_TRACES = 20

FILLER = {"좀", "한번", "제발", "please", "혹시", "그냥"}
PARTICLE = re.compile(r"(?<=[가-힣0-9a-z])(을|를|은|는|이|가)$")

def normalize(s):
    """Same request, same key: case, spacing, punctuation, filler words (좀, 한번) and the object/subject
    particles 을·를·은·는·이·가 at a word's end, and a polite 요 at the end, do not change what is asked.
    (도 is kept: 25도 is a number.)"""
    words = []
    for w in unicodedata.normalize("NFKC", s).lower().replace("한 번", "한번").split():
        w = PUNCT.sub("", w)
        if not w or w in FILLER:
            continue
        if len(w) > 1:
            w = PARTICLE.sub("", w)
        words.append(w)
    out = "".join(words)
    return out[:-1] if out.endswith("요") and len(out) > 2 else out

def numbers(s):
    return [float(x) for x in NUM.findall(unicodedata.normalize("NFKC", s))]

def template(s):
    return NUM.sub("#", normalize(s))

def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")

# ── parameters in an op ───────────────────────────────────────────────────
def parse_params(src):
    """/* PARAMS [...] */ in the op source → list of declarations ([] when absent or broken)."""
    m = re.search(r"/\*\s*PARAMS\s*(\[.*?\])\s*\*/", src, re.S)
    if not m:
        return []
    try:
        ps = json.loads(m.group(1))
        return [p for p in ps if isinstance(p, dict) and re.fullmatch(r"P_[A-Z0-9_]+", str(p.get("name", "")))]
    except ValueError:
        return []

def define_value(src, name):
    m = re.search(rf"^\s*#\s*define\s+{name}\s+\(?\s*(-?(?:0x[0-9a-fA-F]+|\d+))[uUlL]*\s*\)?\s*(?://.*|/\*.*\*/)?$", src, re.M)
    return int(m.group(1), 0) if m else None

def apply_params(src, values):
    for k, v in values.items():
        src = re.sub(rf"^(\s*#\s*define\s+{k}\s+).*$", lambda m: f"{m.group(1)}{int(v):#x}" if k.startswith("POKE_ADDR_") else f"{m.group(1)}{int(v)}", src, count=1, flags=re.M)
    return src

def values_for(params, sentence):
    """Parameter values from the numbers in this sentence; (values, problem_ko)."""
    nums = numbers(sentence)
    vals = {}
    for p in params:
        i = p.get("num")
        if not isinstance(i, int) or i >= len(nums):
            return None, "문장에서 숫자를 찾지 못했습니다"
        raw = nums[i] * p.get("scale", 1)
        if abs(raw - round(raw)) > 1e-6:
            return None, f"{p.get('ko') or p['name']}: {nums[i]:g}은(는) 쓸 수 없는 값입니다 (정수 단위로 말해 주세요)"
        v = int(round(raw))
        lo, hi = p.get("min"), p.get("max")
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            unit = p.get("ko") or p["name"]
            scale = p.get("scale", 1) or 1
            show = lambda x: f"{x / scale:g}"
            rng = f"{show(lo)}–{show(hi)}" if lo is not None and hi is not None else (f"{show(lo)} 이상" if lo is not None else f"{show(hi)} 이하")
            return None, f"{unit}: {rng}만 됩니다 (말한 값 {nums[i]:g})"
        vals[p["name"]] = v
    return vals, None

def check_params(params, src, sentence):
    """Are the declarations consistent with the source and the sentence? → (templated, problems)."""
    probs, nums = [], numbers(sentence)
    seen = set()
    for p in params:
        d = define_value(src, p["name"])
        i = p.get("num")
        if d is None:
            probs.append(f"PARAMS declares {p['name']} but there is no '#define {p['name']} <integer>' line")
            continue
        if not isinstance(i, int) or not (0 <= i < len(nums)):
            probs.append(f"PARAMS {p['name']}: num {i!r} is not the index of a number in the sentence (numbers: {nums})")
            continue
        if int(round(nums[i] * p.get("scale", 1))) != d:
            probs.append(f"PARAMS {p['name']}: #define is {d} but number #{i} of the sentence × scale is {nums[i] * p.get('scale', 1):g}")
        seen.add(i)
    templated = not probs and len(seen) == len(nums) and len(nums) > 0
    return templated, probs

# ── store ──────────────────────────────────────────────────────────────────
def _path(rid):
    return os.path.join(RECORDS, rid + ".json")

import contextlib, fcntl, threading
_HELD = threading.local()

@contextlib.contextmanager
def locked(rid):
    """One writer per record at a time — across threads and processes (flock on a per-record lock file).
    Every read–modify–save of a record happens inside it. Re-entrant within a thread."""
    held = getattr(_HELD, "ids", None)
    if held is None:
        held = _HELD.ids = set()
    if rid in held:
        yield; return
    d = os.path.join(RECORDS, ".locks"); os.makedirs(d, exist_ok=True)
    fd = os.open(os.path.join(d, rid + ".lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        held.add(rid)
        try:
            yield
        finally:
            held.discard(rid)
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)

def save(r):
    os.makedirs(RECORDS, exist_ok=True)
    tmp = f"{_path(r['id'])}.{os.getpid()}.{threading.get_ident()}.{os.urandom(4).hex()}.tmp"   # never shared
    try:
        with open(tmp, "w") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _path(r["id"]))                   # atomic: readers never see half a record
    finally:
        if os.path.exists(tmp): os.remove(tmp)
    return r

def load(rid):
    try:
        return json.load(open(_path(rid)))
    except (OSError, ValueError):
        return None

def all_records():
    out = []
    if os.path.isdir(RECORDS):
        for f in sorted(os.listdir(RECORDS)):
            if f.endswith(".json"):
                r = load(f[:-5])
                if r: out.append(r)
    return out

class Hit(dict):
    """kind: op | need | cannot | range; record; params; src (op with params applied); text (Korean)."""
    __getattr__ = dict.get

def lookup(sentence):
    n, t = normalize(sentence), template(sentence)
    recs = all_records()
    for r in recs:                                         # 1. heard before
        if n in {normalize(s) for s in r.get("sentences", [])}:
            if r.get("kind") == "cannot" and not _same_body(r):
                return None                                # "this body cannot" was said of another body: ask again here
            return _hit(r, sentence)
    for r in recs:                                         # 2. same template, every number a parameter
        if r.get("kind") == "op" and r.get("templated") and template(r["sentences"][0]) == t:   # recomputed: normalization may improve
            return _hit(r, sentence)
    return None

def _same_body(r):
    """Was this record's answer given on the body we have now? (unknown body → assume yes)"""
    was = ((r.get("colophon") or {}).get("body") or {}).get("fp")
    if not was:
        return True
    now = _body() or {}
    return now.get("fp") in (None, was)

def _hit(r, sentence):
    if r.get("kind") in ("need", "cannot", "long", "watch"):
        return Hit(kind=r["kind"], record=r, text=r.get("text", ""), params={})
    vals, prob = values_for(r.get("params", []), sentence)
    if prob:
        return Hit(kind="range", record=r, text=prob, params={})
    return Hit(kind="op", record=r, params=vals, src=apply_params(r["op"], vals))

def _rid(sentence):
    return "r-" + hashlib.sha1(normalize(sentence).encode()).hexdigest()[:10]

def create_op(sentence, src, manifest, colophon):
    with locked(_rid(sentence)):
        return _create_op(sentence, src, manifest, colophon)

def _create_op(sentence, src, manifest, colophon):
    params = parse_params(src)
    templated, _ = check_params(params, src, sentence)
    r = {"id": _rid(sentence), "kind": "op", "sentences": [sentence], "template": template(sentence),
         "params": params, "templated": templated, "op": src, "manifest": manifest,
         "colophon": {"date": _now(), "body": _body(), **colophon}, "approval": None, "traces": [], "lineage": []}
    old = load(r["id"])
    if old and old.get("kind") == "op":                    # a rewrite keeps what was learned about the sentence
        r["sentences"] = old["sentences"] + [s for s in r["sentences"] if s not in old["sentences"]]
        r["traces"] = old.get("traces", [])
        r["origin"] = old.get("origin")
        r["colophon"]["replaces"] = (old.get("colophon") or {}).get("date")
        # 계보: the earlier pronunciation stays with the card (another body may want it back)
        r["lineage"] = (old.get("lineage") or []) + [{"op": old.get("op"), "manifest": old.get("manifest"), "params": old.get("params"),
                                                       "templated": old.get("templated"), "colophon": old.get("colophon"),
                                                       "retired": _now(), "why": colophon.get("why", "")}]
        r["lineage"] = r["lineage"][-10:]
        same_writes = {a.get("fn") for a in (old.get("manifest") or {}).get("uses", [])} == {a.get("fn") for a in (manifest or {}).get("uses", [])}
        r["approval"] = old.get("approval") if same_writes else None    # approval holds only for the same devices
    return save(r)

def create_reply(sentence, kind, text, colophon):
    with locked(_rid(sentence)):
        return _create_reply(sentence, kind, text, colophon)

def _create_reply(sentence, kind, text, colophon):
    r = {"id": _rid(sentence), "kind": kind, "sentences": [sentence], "template": template(sentence),
         "text": text, "colophon": {"date": _now(), "body": _body(), **colophon}, "traces": []}
    return save(r)

def heard(r, sentence):
    """Remember another wording that mapped to this record."""
    with locked(r["id"]):
        r = load(r["id"]) or r
        if normalize(sentence) not in {normalize(s) for s in r["sentences"]}:
            r["sentences"].append(sentence); save(r)
        return r

def approve(r, how):
    with locked(r["id"]):
        r = load(r["id"]) or r
        r["approval"] = {"at": _now(), "how": how}
        return save(r)

def _body():
    try:
        import body
        b = body.current()
        return {**b, "fp": body.fp(b), "label": body.label(b)}
    except Exception:
        return None

def trace(r, sentence, params, boot):
    with locked(r["id"]):
        return _trace(r, sentence, params, boot)

def _trace(r, sentence, params, boot):
    r = load(r["id"]) or r
    b = _body() or {}
    r.setdefault("traces", []).append({"at": _now(), "sentence": sentence, "params": params,
                                       "result": boot.get("result"), "ok": bool(boot.get("powered_off")) and not (boot.get("result") or {}).get("error"),
                                       "node_ms": boot.get("node_ms"), "body": b.get("fp"), "body_label": b.get("label"),
                                       "op_date": (r.get("colophon") or {}).get("date"),
                                       **({"fx": boot["fx_summary"]} if boot.get("fx_summary") else {}),
                                       **({"bench": boot["bench_key"]} if boot.get("bench_key") else {})})
    r["traces"] = r["traces"][-MAX_TRACES:]
    return save(r)

def judge(r, check, adopt):
    """흔적 대조의 판정을 카드에 남김. adopt=False → the new pronunciation goes to the lineage marked
    rejected and the previous one comes back as the card's op (decision 6)."""
    with locked(r["id"]):
        r = load(r["id"]) or r
        stamp = {**check, "at": _now()}
        if adopt or not r.get("lineage"):
            r.setdefault("colophon", {})["trace_check"] = stamp
            return save(r), adopt or not r.get("lineage")
        prev = r["lineage"][-1]
        rejected = {"op": r.get("op"), "manifest": r.get("manifest"), "params": r.get("params"), "templated": r.get("templated"),
                    "colophon": {**(r.get("colophon") or {}), "trace_check": stamp}, "retired": _now(), "why": "흔적 대조 불일치 — 채택 안 함", "rejected": True}
        r["lineage"] = r["lineage"][:-1] + [rejected]
        for k in ("op", "manifest", "params", "templated"):
            r[k] = prev.get(k)
        r["colophon"] = {**(prev.get("colophon") or {}), "kept_over": stamp}
        return save(r), False

def forget(rid):
    with locked(rid):
        try: os.remove(_path(rid)); return True
        except OSError: return False

def find(sentence):
    """The record this exact wording (normalized) belongs to, or None — no templates, no models."""
    n = normalize(sentence)
    return next((r for r in all_records() if n in {normalize(s) for s in r.get("sentences", [])}), None)

def unapprove(r):
    with locked(r["id"]):
        r = load(r["id"]) or r
        r["approval"] = None
        return save(r)

def restore(r, entry):
    """Bring an earlier pronunciation (lineage entry) back as the current op; the current one goes to the lineage."""
    with locked(r["id"]):
        return _restore(r, entry)

def _restore(r, entry):
    r = load(r["id"]) or r
    cur = {"op": r.get("op"), "manifest": r.get("manifest"), "params": r.get("params"), "templated": r.get("templated"),
           "colophon": r.get("colophon"), "retired": _now(), "why": "다른 몸의 발음으로 바꿈"}
    r["lineage"] = [e for e in r.get("lineage", []) if e is not entry and e.get("colophon") != entry.get("colophon")] + [cur]
    for k in ("op", "manifest", "params", "templated", "colophon"):
        r[k] = entry.get(k)
    return save(r)

# ── cards: a record carried to another hub or another body ───────────────────
CARD_KEYS = ("kind", "sentences", "template", "params", "templated", "op", "manifest", "text", "colophon", "traces", "lineage", "origin")

def export_card(r):
    """The record as a portable card. Approval is not carried: it belongs to whoever owns the body."""
    r = load(r["id"]) or r
    return {"poke_card": 1, "exported": _now(), "from_body": _body(), **{k: r.get(k) for k in CARD_KEYS if k in r}}

def import_card(card, replace=False):
    if not isinstance(card, dict) or card.get("poke_card") != 1 or not card.get("sentences"):
        raise ValueError("POKE 카드 형식이 아닙니다")
    with locked(_rid(card["sentences"][0])):
        return _import_card(card, replace)

def _import_card(card, replace=False):
    """→ (record, message_ko). The card's op is kept as it is; whether it fits this body is decided when it runs."""
    if not isinstance(card, dict) or card.get("poke_card") != 1 or not card.get("sentences"):
        raise ValueError("POKE 카드 형식이 아닙니다")
    rid = _rid(card["sentences"][0])
    old = load(rid)
    if old and not replace:
        return old, "이미 같은 문장의 기록이 있습니다 (바꾸려면 replace)"
    r = {"id": rid, **{k: card.get(k) for k in CARD_KEYS if k in card}, "approval": None}
    r["origin"] = {"imported": _now(), "from_body": card.get("from_body"), "exported": card.get("exported")}
    r.setdefault("traces", []); r.setdefault("lineage", [])
    return save(r), "가져왔습니다"

KIND_KO = {"op": "op", "need": "되물음", "cannot": "불가", "long": "예약", "watch": "지켜보기"}

def card_lines(r):
    """A record as the person reads it (더늠 카드)."""
    c, m = r.get("colophon") or {}, r.get("manifest") or {}
    b = c.get("body") or {}
    out = [f"「{r['sentences'][0]}」 ({KIND_KO.get(r['kind'], r['kind'])})"]
    if len(r["sentences"]) > 1:
        out.append("  들은 말: " + " / ".join(r["sentences"][1:]))
    if r["kind"] != "op":
        out.append(f"  답: {r.get('text')}")
    else:
        try:
            import manifest as MF
            acc = MF.access(m)
            out.append("  하는 일: " + (m.get("summary_ko") or "?"))
            out.append("  장치: " + ", ".join(f"{a['dev']}({a['fn']}, {'쓰기' if a['access'] == 'write' else '읽기'})" for a in acc))
        except Exception:
            pass
        if r.get("params"):
            out.append("  숫자: " + ", ".join(f"{p.get('ko') or p['name']} {p.get('min')}–{p.get('max')}" for p in r["params"]))
        out.append("  승인: " + (f"항상 ({r['approval']['at'][:16]})" if r.get("approval") else "매번 확인 (쓰기가 있을 때)"))
    out.append(f"  출처: {c.get('model', '?')} · {str(c.get('date', ''))[:16]} · 카탈로그 {c.get('catalog', '?')} · 몸 {b.get('label', '(기록 전)')}")
    tc = c.get("trace_check") or c.get("kept_over")
    if tc:
        import trace_check as TC
        label = TC.VERDICT_KO.get(tc.get("verdict"), tc.get("verdict"))
        out.append(f"  흔적 대조: {label}" + (" (지금 op는 이전 발음 — 새 발음은 계보에 '채택 안 함'으로)" if c.get("kept_over") else "")
                   + ("".join(f"\n      · {x}" for x in tc.get("reasons") or [])))
    if r.get("origin"):
        fb = (r["origin"].get("from_body") or {}).get("label")
        out.append(f"  가져옴: {str(r['origin'].get('imported', ''))[:16]}" + (f" (원래 몸: {fb})" if fb else ""))
    if r.get("lineage"):
        fb = ((r.get("origin") or {}).get("from_body") or {}).get("label", "몸 미상")
        out.append("  계보: " + " → ".join(f"{((e.get('colophon') or {}).get('body') or {}).get('label', fb)} {str((e.get('colophon') or {}).get('date', ''))[:10]}" for e in r["lineage"])
                   + f" → 지금 ({b.get('label', '?')})")
    tr = r.get("traces") or []
    if tr:
        out.append(f"  결과 흔적 {len(tr)}개 (최근 5):")
        for t in tr[-5:]:
            res = json.dumps(t.get("result"), ensure_ascii=False)
            out.append(f"    {str(t.get('at', ''))[5:16]} {'✓' if t.get('ok') else '✗'} {t.get('body_label') or '(몸 기록 전)'} · {res[:90]}")
    return out

def main(sentence=None):
    if sentence:
        r = find(sentence)
        if not r:
            print(f"「{sentence}」에 대한 기록이 없습니다."); return 1
        print("\n".join(card_lines(r))); return 0
    rs = all_records()
    if not rs:
        print("허브가 기억하는 문장이 아직 없습니다."); return
    print(f"허브가 기억하는 문장 {len(rs)}개 ({RECORDS})")
    for r in rs:
        last = (r.get("traces") or [{}])[-1]
        print(f"  {KIND_KO.get(r['kind'], r['kind']):<4} {'숫자바꿈' if r.get('templated') else '        '} {r['sentences'][0]}"
              + (f"  (+{len(r['sentences']) - 1}가지 말)" if len(r["sentences"]) > 1 else "")
              + (f"  · 실행 {len(r.get('traces', []))}회" if r.get("traces") else "")
              + ("  · 항상 승인" if r.get("approval") else ""))

if __name__ == "__main__":
    main()
