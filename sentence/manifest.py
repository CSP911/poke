"""manifest — what one sentence's op is allowed to touch, declared before it boots.

Format v1 (a JSON object; the model writes it at the top of the op as  /* MANIFEST {...} */):

    {
      "v": 1,
      "uses": [                                   # every fragment function the op calls, once each
        {"fn": "tmp105_read_mC"},
        {"fn": "led_set", "targets": ["P_LED"], "end": "leave"}
      ],
      "max_s": 2,                                 # the op's own run time bound (seconds, ≤ 20)
      "summary_ko": "LED {P_LED}번을 켭니다.",   # one Korean line shown to the person; {P_x} = the number as spoken
      "labels_ko": {"temp_c": "온도 (°C)", "led": "LED 번호", "pin_level": ""}   # optional: result key → Korean label
    }                                             #   "(unit)" at the end becomes the unit; "" hides the key

    fn       a function from the fragment catalog (FN below).
    targets  for writes: which channels (LED numbers, …) — ints, parameter names, or {"from": a, "to": b}.
    end      for writes: what the device is left as — "leave" (the change stays) or "restore".

Everything else is derived on the hub, not trusted from the model:
    access(m)          → per function: device, address, read/write, policy decision
    i2c_allowlist(m)   → the bus-level list the image check / runtime enforces (input to verify V5):
        {"bus": "i2c1", "devices": [{"addr": 72, "read": true, "write_regs": [], "max_write": 1}, …]}
        A 1-byte write is a register-pointer set and counts as part of a read. A longer write is allowed
        only to a device with write access, and only when its first byte is in write_regs.
    decide(m, policy)  → "allow" | "confirm" | "deny" with reasons (Korean)
    cross_check(m, src)→ the op source calls exactly the declared functions (no undeclared calls)

Policy (who decides): the hub owner's table POLICY (defaults below; sentence/policy.json overrides it).
Reads are allowed, writes that change the room ask once, writes that change the node's memory of the
world (the clock) are denied, anything not in the table is denied.
"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))

# fragment function → the bus access it really performs (read off the fragment source)
FN = {
    # tmp105_read_mC sets 12-bit resolution once (config register 0x01) — a configuration write, not an action
    "tmp105_read_mC": {"frag": "tmp105", "dev": "온도 센서", "addr": 0x48, "access": "read",  "write_regs": [0x01], "max_write": 2},
    "rtc_read":       {"frag": "rtc",    "dev": "시계",      "addr": 0x68, "access": "read",  "write_regs": []},
    "rtc_set":        {"frag": "rtc",    "dev": "시계",      "addr": 0x68, "access": "write", "write_regs": [0x00], "max_write": 8},
    # mag_read puts an idle part into continuous mode (CRA, CRB, MODE) — a configuration write, not an action
    "mag_read":       {"frag": "mag",    "dev": "자기 센서", "addr": 0x1E, "access": "read",  "write_regs": [0x00, 0x01, 0x02], "max_write": 2},
    "led_set":        {"frag": "led",    "dev": "LED",       "addr": 0x60, "access": "write", "write_regs": [0x06, 0x07, 0x08, 0x09], "max_write": 2},
    "led_inputs":     {"frag": "led",    "dev": "LED",       "addr": 0x60, "access": "read",  "write_regs": []},
}

POLICY = {
    "tmp105_read_mC": "allow",
    "rtc_read": "allow",
    "mag_read": "allow",
    "led_inputs": "allow",
    "led_set": "confirm",
    "rtc_set": "deny",
}
DENY_WHY = {"rtc_set": "시계를 바꾸는 요청은 허브 정책상 막혀 있습니다"}

# [실기기팀 G3 — added only] functions of parts learned by incubation (sentence/incubation.py writes
# incubated/fn.json after the hub verified the fragment on the twin). They join FN and POLICY; an entry
# above is never replaced. Reads default to allow, anything that changes the room to confirm.
_INCUBATED = os.path.join(os.environ.get("POKE_INCUBATED") or os.path.join(HERE, "incubated"), "fn.json")
if os.path.exists(_INCUBATED):
    try:
        _inc = json.load(open(_INCUBATED))
        for _k, _v in _inc.get("FN", {}).items():
            FN.setdefault(_k, _v)
        for _k, _v in _inc.get("POLICY", {}).items():
            POLICY.setdefault(_k, _v if _v in ("allow", "confirm", "deny") else "deny")
    except (OSError, ValueError):
        pass

def policy():
    p = dict(POLICY)
    f = os.path.join(HERE, "policy.json")
    if os.path.exists(f):
        try: p.update(json.load(open(f)))
        except (OSError, ValueError): pass
    return p

MAX_S = 20

class ManifestError(ValueError):
    pass

def parse(src):
    """The /* MANIFEST {...} */ block at the top of an op, or None."""
    m = re.search(r"/\*\s*MANIFEST\s*(\{.*?\})\s*\*/", src, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError as e:
        raise ManifestError(f"MANIFEST is not valid JSON: {e}")

def validate(m):
    """Shape check. Returns a list of problems (English, for the model's feedback round)."""
    p = []
    if not isinstance(m, dict):
        return ["MANIFEST must be a JSON object"]
    if m.get("v") != 1:
        p.append('MANIFEST needs "v": 1')
    uses = m.get("uses")
    if not isinstance(uses, list):
        p.append('MANIFEST needs "uses": a list of {"fn": …}')
        uses = []
    for u in uses:
        fn = u.get("fn") if isinstance(u, dict) else None
        if fn not in FN:
            p.append(f"MANIFEST uses unknown function {fn!r}")
            continue
        if FN[fn]["access"] == "write" and u.get("end", "leave") not in ("leave", "restore"):
            p.append(f'MANIFEST {fn}: "end" must be "leave" or "restore"')
    s = m.get("max_s", 0)
    if not isinstance(s, (int, float)) or s <= 0 or s > MAX_S:
        p.append(f'MANIFEST "max_s" must be a number in (0, {MAX_S}]')
    if "labels_ko" in m and not (isinstance(m["labels_ko"], dict) and all(isinstance(v, str) for v in m["labels_ko"].values())):
        p.append('MANIFEST "labels_ko" must map result keys to Korean labels (strings)')
    if not isinstance(m.get("summary_ko", ""), str) or not m.get("summary_ko"):
        p.append('MANIFEST needs "summary_ko": one Korean line saying what the op will do')
    return p

def called_functions(src):
    """Fragment functions the op source calls (comments and the MANIFEST block removed)."""
    code = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    code = re.sub(r"//[^\n]*", " ", code)
    return {fn for fn in FN if re.search(rf"\b{fn}\s*\(", code)}

def cross_check(m, src):
    """The op must call exactly what it declared (a declared but unused read is tolerated)."""
    declared = {u["fn"] for u in m.get("uses", []) if isinstance(u, dict) and "fn" in u}
    called = called_functions(src)
    p = [f"the op calls {fn}() but the MANIFEST does not declare it" for fn in sorted(called - declared)]
    p += [f"the MANIFEST declares {fn} (a write) but the op never calls it" for fn in sorted(declared - called)
          if FN.get(fn, {}).get("access") == "write"]
    return p

def access(m):
    return [{"fn": u["fn"], **FN[u["fn"]], **{k: v for k, v in u.items() if k != "fn"}}
            for u in m.get("uses", []) if isinstance(u, dict) and u.get("fn") in FN]

def i2c_allowlist(m):
    """Bus-level allow list derived from the declared functions (never from the model's own words)."""
    devs = {}
    for a in access(m):
        d = devs.setdefault(a["addr"], {"addr": a["addr"], "read": False, "write_regs": set(), "max_write": 1})
        d["read"] = True                                  # every fragment function reads (pointer + data)
        d["write_regs"] |= set(a["write_regs"])
        d["max_write"] = max(d["max_write"], a.get("max_write", 1))
    return {"bus": "i2c1", "devices": [{**d, "write_regs": sorted(d["write_regs"])} for _, d in sorted(devs.items())]}

def writes(m):
    return [a for a in access(m) if a["access"] == "write"]

def decide(m, pol=None):
    """allow / confirm / deny for the whole op, with Korean reasons."""
    pol = pol or policy()
    verdict, why = "allow", []
    for a in access(m):
        d = pol.get(a["fn"], "deny")
        if d == "deny":
            verdict = "deny"; why.append(DENY_WHY.get(a["fn"], f"{a['dev']}({a['fn']})는 허용 목록에 없습니다"))
        elif d == "confirm" and verdict != "deny":
            verdict = "confirm"; why.append(f"{a['dev']}의 상태를 바꿉니다")
    return verdict, why

def fill(text, params, decl=()):
    """{P_x} in summary_ko → the number as the person said it (the #define value ÷ its scale)."""
    scale = {d.get("name"): d.get("scale", 1) or 1 for d in decl or ()}
    def one(k):
        v = params.get(k.group(1))
        if v is None: return k.group(0)
        return f"{v / scale.get(k.group(1), 1):g}"
    return re.sub(r"\{(P_[A-Z0-9_]+)\}", one, text or "")

def summary(m, params=None, decl=()):
    return fill(m.get("summary_ko", ""), params or {}, decl)

if __name__ == "__main__":
    ex = {"v": 1, "uses": [{"fn": "tmp105_read_mC"}, {"fn": "led_set", "targets": [0], "end": "leave"}],
          "max_s": 2, "summary_ko": "온도를 읽고 25도가 넘으면 LED 0번을 켜고, 아니면 끕니다."}
    print(json.dumps(i2c_allowlist(ex)), decide(ex), validate(ex))
