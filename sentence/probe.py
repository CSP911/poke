#!/usr/bin/env python3
"""probe — the hub's hands on the bus, one boot at a time (실기기팀, G2/G3).

There is no bus service on the node any more: nothing of ours stays there. To touch the bus the hub
boots a small fixed sentence image (generated here from a template — no model writes it) that does a
list of transfers and reports the bytes, and the node powers off. Each image carries exactly the I2C
allow list its transfers need (runtime v1, the I2CA table): read access for the addresses it reads,
and for each write longer than one byte, that first byte as a writable register. Nothing else.

Because each call is a fresh power-on, device state does not carry from one call to the next — a
write and the read that checks it belong in one `seq`.

    probe.py scan [FROM TO]                        who answers on bus 1 (0x08–0x77; 8 addresses per boot)
    probe.py read ADDR [REG] N                     REG omitted: read without setting a register pointer
    probe.py write ADDR BYTE [BYTE …]
    probe.py seq "w 3c 80 af; r 4c 00 2; r 20 - 1" [--look]    several transfers in one power-on
    --look   also show what any display on the bench shows after the run (as a person at the bench would see)

Numbers are hex. Output: one line per transfer, `ok` or `err N` (2 = no ACK, 3 = clock stretch, 4 = never finished).
Every call is appended to build/probe/log.jsonl. The bench is bench.active() (POKE_BENCH=ext for the new parts).
"""
import os, sys, json, time, hashlib
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import say, bench

LOG = os.path.join(say.BUILD, "probe", "log.jsonl")
MAX_STEPS, MAX_READ, MAX_DEVS, MAX_REGS = 16, 32, say.I2CA_MAX, say.I2CA_REGS

class ProbeError(RuntimeError):
    pass

def parse_seq(text):
    """'w 3c 80 af; r 4c 00 2; r 20 - 1' → [("w", 0x3c, [0x80, 0xaf]), ("r", 0x4c, 0x00, 2), ("r", 0x20, None, 1)]"""
    steps = []
    for part in [p.strip() for p in text.replace("\n", ";").split(";") if p.strip()]:
        t = part.split()
        if t[0] == "r" and len(t) in (3, 4):
            reg = None if len(t) == 3 or t[2] == "-" else int(t[2], 16)
            n = int(t[-1], 16) if len(t) == 4 else int(t[2], 16)
            steps.append(("r", int(t[1], 16), reg, n))
        elif t[0] == "w" and len(t) >= 3:
            steps.append(("w", int(t[1], 16), [int(x, 16) for x in t[2:]]))
        else:
            raise ProbeError(f"cannot read step {part!r} (use 'r ADDR REG N', 'r ADDR - N' or 'w ADDR B …')")
    return steps

def known_addrs():
    """Addresses of parts the hub already has a fragment for (wherever they are wired) — never written by a probe."""
    try:
        cat = json.load(open(os.path.join(say.FRAGS, "catalog.json")))
    except (OSError, ValueError):
        return set()
    out = set()
    for f, e in cat.items():
        try: out |= {int(str(a), 0) for a in say.wired_addrs(f, int(str(e.get("default_addr")), 16))}
        except (TypeError, ValueError): pass
    return out

def targets():
    """Identification mode (POKE_PROBE_TARGETS="0x4c,0x77", set by incubation for its agent): only these addresses."""
    t = os.environ.get("POKE_PROBE_TARGETS")
    return {int(x, 16) for x in t.split(",") if x.strip()} if t else None

ID_MAX_LONG_WRITES, ID_MAX_WRITE = 8, 32          # identification mode: a cap on what one power-on may write

def guard(steps):
    """Refuse what a probe must never do: in identification mode, any address outside the targets, and more
    than ID_MAX_LONG_WRITES writes of 2+ bytes (each ≤ ID_MAX_WRITE) per power-on. Always: no write of 2+ bytes
    to a part the hub already knows (its allow list drops those registers — the runtime would refuse it)."""
    t = targets()
    if t is not None:
        bad = sorted({s[1] for s in steps} - t)
        if bad:
            raise ProbeError(f"identification may touch only {', '.join(f'0x{a:02x}' for a in sorted(t))}; not {', '.join(f'0x{a:02x}' for a in bad)}")
        longw = [s for s in steps if s[0] == "w" and len(s[2]) > 1]
        if len(longw) > ID_MAX_LONG_WRITES or any(len(s[2]) > ID_MAX_WRITE for s in longw):
            raise ProbeError(f"identification writes at most {ID_MAX_LONG_WRITES} multi-byte writes of ≤ {ID_MAX_WRITE} bytes per power-on")
    k = known_addrs() - (t or set())
    hit = sorted({s[1] for s in steps if s[0] == "w" and len(s[2]) > 1 and s[1] in k})
    if hit:
        raise ProbeError(f"{', '.join(f'0x{a:02x}' for a in hit)} is a part the hub already knows — a probe does not write to it")

def allowlist(steps):
    """Exactly what these transfers need (R3 semantics): an address only read without a register pointer
    gets no write at all ("pointer": false — an unknown part may take one byte as a command); a register
    read adds the 1-byte pointer write; a longer write adds its first byte as a writable register."""
    devs = {}
    for s in steps:
        d = devs.setdefault(s[1], {"addr": s[1], "read": True, "pointer": False, "write_regs": set(), "max_write": 0})
        if s[0] == "r" and s[2] is not None:
            d["pointer"] = True; d["max_write"] = max(d["max_write"], 1)
        if s[0] == "w":
            d["pointer"] = True; d["max_write"] = max(d["max_write"], len(s[2]))
            if len(s[2]) > 1: d["write_regs"].add(s[2][0])
    for a in known_addrs() - (targets() or set()):        # a known part: read with a pointer at most, never written
        if a in devs:
            devs[a]["write_regs"] = set(); devs[a]["max_write"] = min(devs[a]["max_write"], 1)
    if len(devs) > MAX_DEVS: raise ProbeError(f"one power-on can touch at most {MAX_DEVS} addresses")
    for d in devs.values():
        if len(d["write_regs"]) > MAX_REGS: raise ProbeError(f"at most {MAX_REGS} different first bytes per address")
    return {"bus": "i2c1", "devices": [{**d, "write_regs": sorted(d["write_regs"])} for _, d in sorted(devs.items())]}

def op_source(steps):
    if len(steps) > MAX_STEPS: raise ProbeError(f"at most {MAX_STEPS} transfers per power-on")
    tabs, body = [], []
    for i, s in enumerate(steps):
        k = f"s{i}"
        if s[0] == "r":
            if not 1 <= s[3] <= MAX_READ: raise ProbeError(f"read 1..{MAX_READ} bytes")
            body.append(f'    e = i2c_read({s[1]:#x}, {-1 if s[2] is None else s[2]}, b, {s[3]}); rep("{k}", e, b, {s[3]});')
        else:
            if not 1 <= len(s[2]) <= 64: raise ProbeError("write 1..64 bytes")
            tabs.append(f"static const i2c_u8 W{i}[{len(s[2])}] = {{{', '.join(f'{x:#04x}' for x in s[2])}}};")
            body.append(f'    e = i2c_write({s[1]:#x}, W{i}, {len(s[2])}); rep("{k}", e, b, 0);')
    return ('/* probe image: generated by sentence/probe.py from a fixed template (no model) */\n'
            '#include "rt.h"\n#include "i2c_bus.h"\n' + "\n".join(tabs) + '\n'
            'static void rep(const char *k, int e, const i2c_u8 *b, int n) {\n'
            '    char t[3 * 32 + 8]; int j = 0;\n'
            '    if (e) { t[j++] = \'e\'; t[j++] = \'r\'; t[j++] = \'r\'; t[j++] = \' \'; t[j++] = (char)(\'0\' + e); }\n'
            '    else if (!n) { t[j++] = \'o\'; t[j++] = \'k\'; }\n'
            '    else for (int i = 0; i < n; i++) { if (i) t[j++] = \' \'; t[j++] = "0123456789abcdef"[b[i] >> 4]; t[j++] = "0123456789abcdef"[b[i] & 15]; }\n'
            '    t[j] = 0; report_str(k, t);\n}\n'
            'void op_main(void) {\n    i2c_u8 b[32]; int e;\n'
            + "\n".join(body) + "\n}\n")

def build(steps, tag=None, allow=None):
    """The probe image, its allow list patched inside say.build (R3: atomic publish, sidecar = 'given')."""
    src = op_source(steps)
    allow = allow or allowlist(steps)
    tag = tag or "probe-" + hashlib.sha1((src + json.dumps(allow, sort_keys=True)).encode()).hexdigest()[:10]
    data, obj, img = say.build(src, tag, allow=allow)
    c = say.check(obj)
    if not c["ok"]:
        raise ProbeError("the image check refused the probe image: " + "; ".join(c["problems"]))
    return img, allow

def run(steps, look=False, state=None, bench_devs=None, allow=None):
    if allow is None:
        guard(steps)
    img, allow = build(steps, allow=allow)
    b = bench.boot(img, state=state, bench=bench_devs)
    res = b.get("result") or {}
    out = []
    for i, s in enumerate(steps):
        v = res.get(f"s{i}", "(no report)")
        out.append({"step": (s[0], f"{s[1]:#04x}", None if s[0] == "w" or s[2] is None else f"{s[2]:#04x}", s[3] if s[0] == "r" else " ".join(f"{x:02x}" for x in s[2])),
                    "out": v})
    rec = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": [o["step"] for o in out], "out": [o["out"] for o in out],
           "error": res.get("error"), "powered_off": b.get("powered_off"), "allow": allow}
    if look and "screen" in b["truth"].get("ext", {}):
        rec["screen"] = bench.ascii_art(b["truth"]["ext"]["screen"])
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f: f.write(json.dumps(rec) + "\n")
    return rec

def scan(lo=0x08, hi=0x77, bench_devs=None):
    """Addresses that ACK a 1-byte read (no register pointer), 8 per power-on, boots side by side."""
    addrs = list(range(lo, hi + 1))
    chunks = [addrs[i:i + MAX_DEVS] for i in range(0, len(addrs), MAX_DEVS)]
    def one(ch):
        r = run([("r", a, None, 1) for a in ch], bench_devs=bench_devs, allow=say.scan_allowlist(ch))
        return [(a, o) for a, o in zip(ch, r["out"])]
    found, boots = [], 0
    with ThreadPoolExecutor(4) as ex:
        for res in ex.map(one, chunks):
            boots += 1
            found += [a for a, o in res if not o.startswith("err") and o != "(no report)"]
    return sorted(found), boots

def show(rec):
    for s, o in zip(rec["steps"], rec["out"]):
        op, addr, reg, x = s
        print(f"{op} {addr}" + (f" reg {reg}" if reg else "") + (f" n={x}" if op == "r" else f" [{x}]") + f" → {o}")
    if rec.get("error"): print("node:", rec["error"])
    if rec.get("screen"): print("display after the run:\n" + rec["screen"])

if __name__ == "__main__":
    a = sys.argv[1:]
    look = "--look" in a; a = [x for x in a if x != "--look"]
    try:
        if not a: print(__doc__); sys.exit(2)
        if a[0] == "scan":
            lo, hi = (int(a[1], 16), int(a[2], 16)) if len(a) >= 3 else (0x08, 0x77)
            f, n = scan(lo, hi); print(f"answering ({n} power-ons):", " ".join(f"0x{x:02x}" for x in f) or "nothing")
        elif a[0] == "read":
            steps = [("r", int(a[1], 16), None, int(a[2], 16))] if len(a) == 3 else [("r", int(a[1], 16), int(a[2], 16), int(a[3], 16))]
            show(run(steps, look))
        elif a[0] == "write":
            show(run([("w", int(a[1], 16), [int(x, 16) for x in a[2:]])], look))
        elif a[0] == "seq":
            show(run(parse_seq(" ".join(a[1:])), look))
        else:
            print(__doc__); sys.exit(2)
    except ProbeError as e:
        print("probe:", e); sys.exit(1)
