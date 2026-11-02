"""body — what the hub is talking to right now, and whether a remembered op still fits it (몸 지문).

The sentence and its result traces belong to the record; an op is only one pronunciation of the sentence
for one body. When the body changes (a part wired at another address, a fragment missing, another bench),
the hub checks the op against the body and, if it does not fit, pronounces the sentence again.

    current()            → {"bench", "parts": [[model, addr]…], "wiring": {frag: [addr…]}, "catalog": [frag…]}
    fp(b)                → short fingerprint
    label(b)             → Korean name ("기본 실험대", "기본 실험대 · TMP105 0x4a")
    fits(op_src, manifest) → (True, []) or (False, [Korean reasons])
    load_body_file(path) → apply a twin body description to this process (say.py --body FILE):
        {"name": "TMP105를 0x4a로 옮긴 몸", "wiring": {"tmp105": ["0x4a"]}, "move": {"t1": "0x4a"}}
"""
import hashlib, json, os, re, tempfile

NAMES = {"tmp105": "TMP105", "ds1338": "DS1338", "lsm303dlhc_mag": "LSM303", "pca9552": "PCA9552",
         "tmp421": "TMP421", "pca9554": "PCA9554", "ssd0303": "SSD0303"}
_NAME = {"name": None}

def current():
    import say, bench as BN
    return {"bench": BN.name(), "parts": sorted([m, a] for m, _, a in say.BENCH),
            "wiring": {k: sorted(v) for k, v in sorted(say.wiring().items())},
            "catalog": sorted(say.catalog()), "name": _NAME["name"]}

def fp(b=None):
    b = b or current()
    core = {k: b.get(k) for k in ("bench", "parts", "wiring", "catalog")}
    return hashlib.sha1(json.dumps(core, sort_keys=True).encode()).hexdigest()[:10]

def label(b=None):
    import bench as BN
    b = b or current()
    if b.get("name"):
        return b["name"]
    base = {m: a for m, _, a in BN.BASE}
    parts = {m: a for m, a in b.get("parts", [])}
    bits = ["기본 실험대" if b.get("bench") == "base" else "확장 실험대"]
    for m, a in sorted(parts.items()):
        if m in base and base[m] != a:
            bits.append(f"{NAMES.get(m, m)} 0x{a:02x}")
    for f, addrs in (b.get("wiring") or {}).items():
        s = f"{f.upper()} " + ",".join(f"0x{int(x):02x}" for x in addrs)
        if not any(s.split()[1] in x for x in bits): bits.append("배선 " + s)
    return " · ".join(bits)

def diff(a, b):
    """Korean lines: how body b differs from body a (for the person and for the model)."""
    out = []
    pa, pb = {m: x for m, x in a.get("parts", [])}, {m: x for m, x in b.get("parts", [])}
    for m in sorted(set(pa) | set(pb)):
        if m not in pb: out.append(f"{NAMES.get(m, m)} 없음")
        elif m not in pa: out.append(f"{NAMES.get(m, m)} 새로 있음 (0x{pb[m]:02x})")
        elif pa[m] != pb[m]: out.append(f"{NAMES.get(m, m)} 0x{pa[m]:02x} → 0x{pb[m]:02x}")
    if (a.get("wiring") or {}) != (b.get("wiring") or {}):
        out.append(f"배선 {a.get('wiring') or '기본'} → {b.get('wiring') or '기본'}")
    ca, cb = set(a.get("catalog") or []), set(b.get("catalog") or [])
    if ca - cb: out.append("조각 없음: " + ", ".join(sorted(ca - cb)))
    return out

# ── does an op fit this body? ───────────────────────────────────────────────
def _addr_args(src):
    """{fn: [address expression of the first argument, …]} for every fragment call in the op."""
    import manifest as MF
    code = re.sub(r"/\*.*?\*/|//[^\n]*", " ", src, flags=re.S)
    out = {}
    for fn in MF.FN:
        for m in re.finditer(rf"\b{fn}\s*\(\s*([^,\)]+)", code):
            out.setdefault(fn, []).append(m.group(1).strip())
    return out

def _value(expr, src):
    expr = expr.strip("() ")
    if re.fullmatch(r"0x[0-9a-fA-F]+|\d+", expr):
        return int(expr, 0)
    m = re.search(rf"^\s*#\s*define\s+{re.escape(expr)}\s+\(?\s*(0x[0-9a-fA-F]+|\d+)", src, re.M)
    return int(m.group(1), 0) if m else None

def fits(src, manifest=None):
    """Can this op run on the current body as it is? Addresses through POKE_ADDR_* constants are the
    hub's and always fit; a literal address must be where its part is wired; every fragment must exist."""
    import say, manifest as MF
    cat = say.catalog()
    why = []
    for u in (manifest or {}).get("uses", []):
        frag = MF.FN.get(u.get("fn"), {}).get("frag")
        if frag and frag not in cat:
            why.append(f"이 몸에는 '{frag}' 조각이 없습니다")
    for fn, exprs in _addr_args(src).items():
        e = MF.FN[fn]
        wired = [int(str(a), 0) for a in say.wired_addrs(e["frag"], e["addr"])]
        for x in exprs:
            if x.startswith("POKE_ADDR_"):
                continue
            v = _value(x, src)
            if v is not None and v not in wired:
                why.append(f"{e['dev']}를 0x{v:02x}에서 찾지만 이 몸에서는 " + ", ".join(f"0x{w:02x}" for w in wired) + "에 있습니다")
    return (not why), sorted(set(why))

# ── a twin body from a file (CLI --body / ui POKE_BODY) ─────────────────────
def load_body_file(path):
    import say
    d = json.load(open(path))
    if d.get("wiring") is not None:
        w = os.path.join(tempfile.mkdtemp(prefix="poke-body-"), "wiring.json")
        json.dump(d["wiring"], open(w, "w"))
        say.WIRING = w
    moves = {q: int(str(a), 0) for q, a in (d.get("move") or {}).items()}
    if moves:
        say.BENCH = [(m, q, moves.get(q, a)) for m, q, a in say.BENCH]
    _NAME["name"] = d.get("name")
    return current()
