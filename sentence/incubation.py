#!/usr/bin/env python3
"""incubation — a part the hub has never met joins the body, and sentences can use it (실기기팀, G2/G3).

    ../sim/body/.venv/bin/python incubation.py discover            # scan the bus, compare with what the hub knows
    ../sim/body/.venv/bin/python incubation.py run [ADDR …]        # discover → identify → fragment → verify → register
    ../sim/body/.venv/bin/python incubation.py verify NAME         # re-run a registered fragment's verification
    ../sim/body/.venv/bin/python incubation.py amend NAME …        # add the required NAME_identify() to a part learned before it was required

The bench is POKE_BENCH (incubation sets "ext" unless told otherwise). Nothing of this stays on the node:
the probe images and the test images are sentence boots like any other (runtime v1, allow lists).

1 discover   probe.scan(): 14 power-ons of 8 addresses (read-only allow lists). Addresses not in the
             fragment catalog and not incubated before are unknown.
2 identify   Claude Code as an agent whose only tool is probe.py (bus reads and writes, one power-on each,
             plus what a person at the bench would see on a display). It ends with a JSON verdict.
3 fragment   Claude Code (claude -p) writes, for each identified part, a fragment header (frags/ format),
             the bus access each function needs (→ manifest.FN), and verification tests in a fixed schema.
4 verify     The hub — not the model — runs the tests on the twin: it picks the bench knobs and
             parameters itself (two different draws per test), builds each test image with the allow
             list the declared access gives, and compares the report with the bench's real state
             (QOM knobs; the display's screendump). Failures go back to the same session (≤ 3 rounds).
5 register   frags/<name>.h, catalog.json (listed on the bench it was learned on), incubated/fn.json
             (manifest.FN and policy entries, added — never changing an existing one), and the
             record of what the hub decided to remember: incubated/<name>.json (who, when, evidence,
             verification draws and results).
"""
import os, re, sys, ast, json, time, random, shutil, hashlib, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("POKE_BENCH", "ext")
import say, bench, probe
import claude_cli

INC = os.environ.get("POKE_INCUBATED") or os.path.join(HERE, "incubated")   # isolated copy for reliability runs
FN_JSON = os.path.join(INC, "fn.json")
STAGE = os.path.join(say.BUILD, "incubate")

def stage_dir():
    """Work files of THIS run: build/incubate/<job> (learn.py sets POKE_LEARN_JOB), else build/incubate/manual-<pid> —
    two runs never write each other's identify / cost files."""
    d = os.path.join(STAGE, os.environ.get("POKE_LEARN_JOB") or f"manual-{os.getpid()}")
    os.makedirs(d, exist_ok=True)
    return d
CAT = os.path.join(say.FRAGS, "catalog.json")
PY = os.path.relpath(sys.executable, HERE) if sys.executable.startswith(os.path.dirname(HERE)) else sys.executable

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

# progress events for a learning job (H10, learn.py): one JSON line per stage to POKE_LEARN_EVENTS
def emit(stage, **kw):
    f = os.environ.get("POKE_LEARN_EVENTS")
    if not f:
        return
    with open(f, "a") as fh:
        fh.write(json.dumps({"t": round(time.time(), 2), "stage": stage, **kw}, ensure_ascii=False, default=str) + "\n")

# what one run costs (B3): model calls, power-ons, wall time
STATS = {"model_calls": 0, "agent_calls": 0, "power_ons": 0, "t0": time.time()}
_ask, _agent, _boot = claude_cli.ask, claude_cli.agent, bench.boot
def _count_ask(*a, **k): STATS["model_calls"] += 1; return _ask(*a, **k)
def _count_agent(*a, **k): STATS["model_calls"] += 1; STATS["agent_calls"] += 1; return _agent(*a, **k)
def _count_boot(*a, **k): STATS["power_ons"] += 1; return _boot(*a, **k)
claude_cli.ask, claude_cli.agent, bench.boot = _count_ask, _count_agent, _count_boot

# ── 1 discover ─────────────────────────────────────────────────────────────
def known_addrs():
    """Addresses the hub already has a fragment for (every catalog entry, whatever bench it was learned on)."""
    cat = json.load(open(CAT))
    out = {}
    for name, e in cat.items():
        try: out[int(str(e.get("default_addr")), 16)] = name
        except (TypeError, ValueError): pass
    return out

def discover():
    found, boots = probe.scan()
    known = known_addrs()
    unknown = [a for a in found if a not in known]
    return {"answering": found, "power_ons": boots, "known": {f"0x{a:02x}": known[a] for a in found if a in known},
            "unknown": unknown}

# ── 2 identify ─────────────────────────────────────────────────────────────
BRIEF = """You are incubating hardware on POKE, a bare-metal node with no operating system. The hub found I2C devices on bus 1 at addresses it has no driver for. Nobody will tell you what they are. Your only instrument is this command, run from the current directory:

  {py} probe.py read ADDR [REG] N          (REG omitted = read without setting a register pointer)
  {py} probe.py write ADDR BYTE [BYTE …]
  {py} probe.py seq "w ADDR B …; r ADDR REG N; r ADDR - N" [--look]
  (numbers in hex; --look also prints what any display on the bench shows after the run, as a person at the bench would see it)

Each command is one fresh power-on of the node: device state does NOT carry over between commands. Do a write and the read or look that checks it in one `seq` (up to 16 transfers, 8 addresses, reads of up to 32 bytes, writes of up to 64 bytes).

Work like an engineer at a bench: for each address use what you know about common I2C parts at that address — ID / manufacturer registers, register layouts, value ranges, reset values, auto-increment — and read whatever confirms or refutes a hypothesis. Prefer reads; write only when a read cannot settle it. Some parts cannot be read at all (a write-only display controller, for instance): then find what a write does. Report readings in physical units. Keep it efficient: a few dozen commands in total.

When done, end your reply with exactly one fenced block:
```json
[{{"addr": "0x..", "identity": "...", "part": "<chip name>", "confidence": 0.0-1.0, "evidence": "the reads/writes that decided it", "registers": "the register map / protocol you established, concise", "current_reading": "..."}}]
```"""

def identify(unknown):
    open(probe.LOG, "a").close()
    n0 = sum(1 for _ in open(probe.LOG))
    addrs = ", ".join(f"0x{a:02x}" for a in unknown)
    log(f"[identify] Claude Code probes {addrs} — its only tool: probe.py")
    emit("identify", state="start", addrs=[f"0x{a:02x}" for a in unknown])
    t0 = time.time()
    os.environ["POKE_PROBE_TARGETS"] = ",".join(f"0x{a:02x}" for a in unknown)   # its probe.py: these addresses only
    out = claude_cli.agent(f"Identify the I2C devices at {addrs} on this POKE node's bus 1 (the other addresses are known parts; leave them alone).",
                           [f"Bash({PY} probe.py:*)"], system=BRIEF.format(py=PY), cwd=HERE, timeout=2400)
    text = out.get("result", "")
    calls = [json.loads(l) for i, l in enumerate(open(probe.LOG)) if i >= n0 and l.strip()]
    m = re.findall(r"```json\s*(\[.*?\])\s*```", text, re.S)
    verdict = json.loads(m[-1]) if m else []
    rec = {"seconds": round(time.time() - t0), "power_ons": len(calls), "verdict": verdict, "calls": calls,
           "transcript_tail": text[-4000:], "cost_usd_shown": out.get("total_cost_usd")}
    say.write_json_atomic(os.path.join(stage_dir(), "identify.json"), rec, indent=1, ensure_ascii=False)
    log(f"[identify] {len(calls)} power-ons, {rec['seconds']} s")
    emit("identify", state="done", power_ons=len(calls), seconds=rec["seconds"],
         verdict=[{k: d.get(k) for k in ("addr", "part", "identity", "confidence")} for d in verdict])
    for d in verdict:
        log(f"   {d.get('addr')}: {d.get('part')} — {str(d.get('identity'))[:70]} ({float(d.get('confidence', 0)):.0%})")
    return rec

# ── 3 fragment ─────────────────────────────────────────────────────────────
FRAG_SYSTEM = """You turn an identified I2C part into a FRAGMENT for POKE sentence images, and you write the tests the hub will use to verify it on the twin.

POKE: a bare-metal node with no OS. For each sentence the hub builds one image: a fixed runtime, the fragments the sentence needs, and an op (`void op_main(void)`) written for that sentence. The op and its fragments run unprivileged and reach the bus only through the runtime; each transfer is checked against the image's allow list, which the hub derives from what each fragment function declares it touches.

Answer with exactly these fenced blocks, in this order, and nothing else:

1. ```json
{"name": "<short lowercase C identifier, the fragment's name, e.g. tmp421>",
 "dev_ko": "<what a Korean speaker calls this part, e.g. 원격 온도 센서>",
 "use_when": "<3-5 situations separated by ' · ', in an agent's words>",
 "functions": [{"sig": "static int <name>_<verb>(unsigned addr, …)", "doc": "<one line: units, ranges, 0 = ok>",
                "access": "read" | "write", "write_regs": [<first bytes of every write longer than 1 byte this function makes>], "max_write": <longest write in bytes>,
                "policy": "allow" | "confirm"}],
 "tests": [{"name": "...",
            "knobs": {"<knob>": {"min": <int>, "max": <int>} | {"choices": [...]}},
            "params": {"<P_NAME>": {"min": <int>, "max": <int>}},
            "text": false,
            "expect": {"<report key>": "<expression>"}, "tol": {"<report key>": <number>},
            "expect_knobs": {"<knob name, may contain {P_NAME}>": "<expression>"},
            "expect_range": {"<report key>": [<lowest physically plausible>, <highest>]}}]}
```
2. ```c  the fragment header ```
3. one ```c block per test: the test op ```

The fragment header:
- First line exactly: // FRAG {"name": "<name>", "use_when": "<same as above>", "functions": ["<signature>", ...]}
- Include guard, then #include "i2c_bus.h": i2c_init() (call before the first transfer; harmless again), i2c_read(addr, reg, buf, n) (reg -1 = no pointer), i2c_write(addr, data, n) (n ≤ 64); types i2c_u8 / i2c_u32; return 0 ok, 1 bad request, 2 no ACK, 3 clock stretch, 4 never finished.
- Every function `static`, address as a parameter, returns 0 or the i2c error. No globals except `static const` tables. No printf, no memcpy/memset, no struct copies, no big local initialisers, no floating point.
- A one-byte write is a register-pointer set (always allowed with read access). A longer write must start with a byte you list in that function's write_regs, and be no longer than its max_write. Write only what the function needs.
- Functions that only read get "access": "read" (a mode/config write needed to read still counts as read, list its registers); functions that change what the part does in the room (an output pin, what a display shows) get "access": "write" and "policy": "confirm".
- Cover what a person would ask of this part in a sentence (read the value(s); for an output: set one pin / show a short text). Keep it to 1–3 functions, plus identify:
- REQUIRED: `static int <name>_identify(unsigned addr)` with "access": "read", "write_regs": [] — returns 1 if the part at addr is this part, 0 if it is not, a negative number if it cannot tell. Use what only this part answers: a manufacturer / device ID register, a fixed revision code, a register's reset value no other common part has. Read only (register pointer writes are fine; nothing that changes the part). The hub runs it at this part's address (must be 1) and at every other part on the bench (must not be 1). If the part has nothing readable at all (a write-only display), make it return -1 and say so in its "doc" — the hub then records that this part cannot be recognised if it is moved.

The tests (the hub runs each twice with different draws; it, not you, picks the values):
- "knobs": the twin's controls for this part (listed below) that the hub sets before power-on, with the range to draw from.
- "params": numbers the hub draws and writes as `#define P_NAME <value>` at the top of the test op.
- "text": true → the hub writes `#define SCREEN_TEXT "<text>"` (2–8 characters from A–Z, 0–9 and space) and checks that the display shows that text: one glyph per character, the same character drawn the same way, different characters differently. Your test op must draw SCREEN_TEXT with a 5×7-style font from your fragment, left to right on one line.
- "expect": expressions over the drawn knob and parameter values (Python syntax: names, numbers, + - * / //, %, comparisons, `a if c else b`, abs, round) compared with the report (numbers within "tol"); "expect_knobs": the knob values after the run (e.g. a pin the op drove).
- A part with no twin controls (the list below says none): the hub cannot set what it measures, so write "expect_range" with the physically plausible span of each reported value (e.g. air pressure in Pa, temperature in m°C) — and report the raw readings too (keys ending in _raw). The hub then asks a second, independent reader to compute the physical values from the part's registers, and compares.
- The test op: #include "rt.h" and your header; `void op_main(void)`; reports with report_int / report_str / report_fixed (rt.h: void report_int(const char *key, long v); void report_str(const char *key, const char *s); void report_fixed(const char *key, long v, int decimals); void say(const char *s); void fail(const char *why)); on an error, fail("…") and return. Use the address given below.
- Make the tests decisive: they must fail if the fragment reads the wrong register, decodes wrongly, or drives the wrong pin."""

def knobs_of(qid):
    """The twin's controls for a bench part (QOM properties) — handed to the fragment writer for its tests."""
    import socket
    port = 20000 + int.from_bytes(os.urandom(2), "little") % 20000
    devs = sum((["-device", f"{m},id={q},bus=i2c-bus.1,address={a:#x}"] for m, q, a in bench.active()), [])
    img = probe.build([("r", 0x48, None, 1)])[0]
    qp = subprocess.Popen(["qemu-system-aarch64", "-M", "raspi4b", "-kernel", img, "-display", "none", "-S", "-serial", "null",
                           "-qmp", f"tcp:127.0.0.1:{port},server=on,wait=off", "-monitor", "none", *devs],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        mon = say.QMP(port)
        props = mon.call("qom-list", path=f"/machine/peripheral/{qid}")
        out = {}
        for p in props:
            n, t = p["name"], p["type"]
            if n in ("type", "parent_bus", "realized", "hotplugged", "hotpluggable", "address", "description") or n.startswith("unnamed-"):
                continue
            try:
                now = mon.get(qid, n)
                out[n] = {"type": "string, 'high' or 'low' (set = drive the pin as an input; read = its level)" if now in ("high", "low") else t, "now": now}
            except RuntimeError: pass
        mon.call("quit")
        return out
    finally:
        try: qp.wait(5)
        except subprocess.TimeoutExpired: qp.kill(); qp.wait()

def fragment_prompt(d, addr, knobs, calls):
    mine = [c for c in calls if any(s[1] == f"0x{addr:02x}" for s in c["steps"])][-40:]
    lines = []
    for c in mine:
        lines.append("; ".join(f"{s[0]} {s[1]}" + (f" {s[2]}" if s[2] else "") + f" {s[3]}" for s in c["steps"]) + " → " + " | ".join(map(str, c["out"])))
        if c.get("screen"): lines.append("  display:\n" + c["screen"])
    kn = "\n".join(f"  {k}: {v['type']} (now {v['now']!r})" for k, v in knobs.items()) or "  (none: the part's state is not settable on the twin)"
    return (f"The part at address 0x{addr:02x} (use this address in the tests):\n{json.dumps(d, ensure_ascii=False, indent=1)}\n\n"
            f"The probe session that identified it (one power-on per line):\n" + "\n".join(lines) +
            f"\n\nThe twin's controls for this part (knobs the hub can set before power-on and read after):\n{kn}")

def parse_answer(text):
    js = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S)
    cs = re.findall(r"```c\s*\n(.*?)```", text, re.S)
    if not js or not cs:
        raise ValueError("expected a ```json block and ```c blocks")
    meta = json.loads(js.group(1))
    header, ops = cs[0], cs[1:]
    if len(ops) != len(meta.get("tests", [])):
        raise ValueError(f"{len(meta.get('tests', []))} tests but {len(ops)} test ops")
    return meta, header, ops

# ── 4 verify ───────────────────────────────────────────────────────────────
OK_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.Name, ast.Load, ast.Constant,
            ast.Call, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
            ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.BitAnd, ast.BitOr, ast.RShift, ast.LShift)
FUNCS = {"abs": abs, "round": round, "int": int}

def evaluate(expr, env):
    if not isinstance(expr, str):
        return expr
    t = ast.parse(expr, mode="eval")
    for n in ast.walk(t):
        if not isinstance(n, OK_NODES) or (isinstance(n, ast.Call) and not (isinstance(n.func, ast.Name) and n.func.id in FUNCS)):
            raise ValueError(f"expression not allowed: {expr!r}")
    return eval(compile(t, "<expect>", "eval"), {"__builtins__": {}, **FUNCS}, dict(env))

def glyphs(rows):
    """Split a 1-bit screen into glyphs: runs of lit columns; gaps ≥ 3 blank columns are spaces."""
    lit = [any(r[x] for r in rows) for x in range(len(rows[0]))]
    out, x, gap = [], 0, 0
    while x < len(lit):
        if lit[x]:
            s = x
            while x < len(lit) and lit[x]: x += 1
            if out and gap >= 3: out.append(" ")
            out.append(tuple(tuple(r[s:x]) for r in rows)); gap = 0
        else:
            gap += 1; x += 1
    return out

def check_screen(runs):
    """runs: [(text, rows)] — one glyph per non-space character, consistent and distinct across runs."""
    table, why = {}, []
    for text, rows in runs:
        g = [x for x in glyphs(rows) if x != " "]
        chars = [c for c in text if c != " "]
        if len(g) != len(chars):
            why.append(f"{text!r}: {len(g)} glyphs on the screen for {len(chars)} characters"); continue
        for c, x in zip(chars, g):
            if c in table and table[c] != x: why.append(f"{c!r} drawn two different ways")
            table.setdefault(c, x)
    seen = {}
    for c, x in table.items():
        if x in seen and seen[x] != c: why.append(f"{c!r} and {seen[x]!r} drawn the same")
        seen[x] = c
    return why

def draw(spec, rnd):
    if "choices" in spec: return rnd.choice(spec["choices"])
    return rnd.randint(int(spec["min"]), int(spec["max"]))

def run_test(name, meta, header, op_src, test, addr, seed, knob_now=None):
    """Two draws of one test on the twin. Returns (ok, [problems], [records])."""
    rnd = random.Random(seed)
    knob_now = {k: v.get("now") for k, v in (knob_now or {}).items()}
    stage = os.path.join(stage_dir(), name); os.makedirs(stage, exist_ok=True)
    open(os.path.join(stage, f"{name}.h"), "w").write(header)
    texts = ["HELLO 42", "OK 7 GO", "POKE", "AB 12", "Z9 X"]
    rnd.shuffle(texts)
    allow = {"bus": "i2c1", "devices": [{"addr": addr, "read": True,
             "write_regs": sorted({r for f in meta["functions"] for r in f.get("write_regs", [])}),
             "max_write": max([f.get("max_write", 1) for f in meta["functions"]] + [1])}]}
    if len(allow["devices"][0]["write_regs"]) > say.I2CA_REGS:
        return False, [f"the functions declare {len(allow['devices'][0]['write_regs'])} writable first bytes; the runtime table holds {say.I2CA_REGS}"], []
    recs, why, screens = [], [], []
    cflags = say.CFLAGS
    say.CFLAGS = cflags[:-4] + ["-I", stage] + cflags[-4:]            # the candidate header first
    try:
        for k in range(2):
            vals = {kn: draw(sp, rnd) for kn, sp in test.get("knobs", {}).items()}
            vals.update({p: draw(sp, rnd) for p, sp in test.get("params", {}).items()})
            defs = "".join(f"#define {p} {vals[p]}\n" for p in test.get("params", {}))
            text = texts[k] if test.get("text") else None
            if text: defs += f'#define SCREEN_TEXT "{text}"\n'
            src = defs + op_src
            tag = f"inc-{name}-" + hashlib.sha1(src.encode()).hexdigest()[:8]
            try:
                data, obj, img = say.build(src, tag, allow=allow)        # allow list patched inside build: atomic publish
            except RuntimeError as e:
                return False, [f"test {test['name']!r} does not build: {str(e)[-700:]}"], recs
            c = say.check(obj)
            if not c["ok"]:
                return False, [f"test {test['name']!r}: the image check refused it: " + "; ".join(c["problems"][:4])], recs
            state = bench.default_state()
            qid = next(q for m, q, a in bench.active() if a == addr)
            def qv(v, now):                                   # a GPIO knob takes "high"/"low", whatever the test drew
                if isinstance(now, str) and now in ("high", "low") and not isinstance(v, str):
                    return "high" if v else "low"
                return v
            state.update({f"{qid} {kn}": qv(v, knob_now.get(kn)) for kn, v in vals.items() if kn in test.get("knobs", {})})
            try:
                b = bench.boot(img, state=state)
            except RuntimeError as e:
                return False, [f"test {test['name']!r}: the bench refused a knob value: {e}"], recs
            res = b.get("result") or {}
            ext = b["truth"].get("ext", {})
            rec = {"draw": vals, "text": text, "result": res, "powered_off": b.get("powered_off"), "lines": b["lines"][-6:],
                   "effects": b.get("effects") or say.parse_effects(b.get("lines", []))}       # V7 reads what the run wrote
            if res.get("error"):
                why.append(f"test {test['name']!r} draw {vals}: the node reported error {res['error']!r} (lines: {b['lines'][-4:]})")
            for key, expr in test.get("expect", {}).items():
                try: want = evaluate(expr, vals)
                except Exception as e: why.append(f"expect {key}: {e}"); continue
                got = res.get(key)
                tol = float(test.get("tol", {}).get(key, 0))
                if isinstance(want, (int, float)) and not isinstance(want, bool):
                    try: good = got is not None and abs(float(got) - float(want)) <= tol
                    except (TypeError, ValueError): good = False
                else:
                    good = str(got) == str(want)
                rec.setdefault("checks", []).append({"key": key, "want": want, "got": got, "ok": good})
                if not good: why.append(f"test {test['name']!r} draw {vals}: report {key} = {got!r}, the bench says {want!r} (tol {tol})")
            for key, span in (test.get("expect_range") or {}).items():
                got = res.get(key)
                try: good = got is not None and float(span[0]) <= float(got) <= float(span[1])
                except (TypeError, ValueError, IndexError): good = False
                rec.setdefault("checks", []).append({"key": key, "range": span, "got": got, "ok": good})
                if not good: why.append(f"test {test['name']!r}: report {key} = {got!r}, outside the plausible {span}")
            if test.get("expect_knobs"):
                # knobs after the run, read in a follow-up boot is not needed: bench.truth has the GPIO pins
                pins = {f"pin{i}": v for i, v in ext.get("pca9554_pins", {}).items()}
                for kexpr, expr in test["expect_knobs"].items():
                    kn = kexpr.format(**vals)
                    want = evaluate(expr, vals)
                    got = pins.get(kn)
                    if got in ("high", "low") and not isinstance(want, str):
                        want = "high" if want else "low"
                    good = str(got) == str(want)
                    rec.setdefault("checks", []).append({"knob": kn, "want": want, "got": got, "ok": good})
                    if not good: why.append(f"test {test['name']!r} draw {vals}: after the run {kn} = {got!r}, expected {want!r}")
            if text:
                rows = ext.get("screen")
                rec["screen"] = bench.ascii_art(rows) if rows else None
                if rows: screens.append((text, rows))
                else: why.append("no display on the bench to check")
            recs.append(rec)
    finally:
        say.CFLAGS = cflags
    if test.get("text") and screens:
        sw = check_screen(screens)
        why += [f"test {test['name']!r}: {w}" + "".join(f"\n  screen for {t!r}:\n" + bench.ascii_art(r) for t, r in screens[:1]) for w in sw[:3]]
    return not why, why, recs

# ── 3+4 with feedback, 5 register ──────────────────────────────────────────
BENCH_TOOL = """
While you work you may use the bench yourself — the same instrument the identification used (one fresh power-on per command; do a write and its check in one seq):
  {py} probe.py read ADDR [REG] N
  {py} probe.py write ADDR BYTE [BYTE …]
  {py} probe.py seq "w ADDR B …; r ADDR REG N" [--look]     (--look: what any display shows after the run)
Use it to settle the protocol before you write the fragment (e.g. what exact byte sequence makes a display show a pixel where you expect it). Then answer with the blocks."""

ORACLE = """You are checking another engineer's driver without seeing it. Given an I2C part's identity and a dump of
its registers read right after power-on (no configuration written), compute the physical values the driver should report,
using the part's datasheet (calibration coefficients, compensation formulas, scale factors). Show the key steps briefly,
then end with exactly one fenced ```json block: {"<report key>": <number>, ...} for the keys asked — or {"cannot": "<why>"}."""

def oracle_check(d, addr, meta, results, log_=None):
    """For a part the twin cannot set (no knobs): an independent second reading. The hub dumps registers
    0x00–0x3f (read-only, the part is identified) and asks a fresh Claude Code session — which never saw the
    fragment — to compute the values; the fragment's report must agree within 1 %."""
    import probe
    rec = probe.run([("r", addr, 0x00, 32), ("r", addr, 0x20, 32)])
    dump = " ".join(rec["out"])
    keys = sorted({k for r in results for dr in r.get("draws", []) for k, v in (dr.get("result") or {}).items()
                   if isinstance(v, (int, float)) and not k.endswith("_raw") and not isinstance(v, bool)})
    docs = "\n".join(f"  {f['sig']}  /* {f.get('doc', '')} */" for f in meta["functions"])
    q = (f"Part: {d.get('part')} — {d.get('identity')}\nAddress 0x{addr:02x}. Registers 0x00..0x3f (hex, in order):\n{dump}\n\n"
         f"The driver's functions (units in the comments):\n{docs}\n\nCompute these report keys: {keys}. "
         f"Units follow the key names (…_pa pascal, …_mc / …_mC milli-degrees C, …_c degrees C, …_hpa hectopascal).")
    text = claude_cli.ask(ORACLE, q)
    m = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    want = json.loads(m[-1]) if m else {"cannot": "no answer block"}
    if "cannot" in want:
        return None, {"oracle": want, "dump": dump}
    got = next((dr.get("result") for r in results for dr in r.get("draws", []) if dr.get("result")), {}) or {}
    cmp_, ok = [], True
    for k, v in want.items():
        if k not in got or not isinstance(v, (int, float)):
            continue
        good = abs(float(got[k]) - float(v)) <= max(abs(float(v)) * 0.01, 1.0)
        cmp_.append({"key": k, "fragment": got[k], "independent": v, "ok": good}); ok &= good
    if not cmp_:
        return None, {"oracle": want, "dump": dump, "note": "no key in common"}
    return ok, {"compare": cmp_, "dump": dump, "oracle_tail": text[-1500:]}

def identify_fn(meta):
    for f in meta.get("functions", []):
        m = re.search(r"\b(\w+_identify)\s*\(", f.get("sig", ""))
        if m: return m.group(1)
    return None

def run_identify(name, header, meta, addr):
    """The hub's own identify test: 1 at the part's address, not 1 at every other part on the bench
    (all in one power-on; each address read-only with a pointer). → (ok, problems, record, kind)"""
    fn = identify_fn(meta)
    if not fn:
        return False, [f"the fragment has no {name}_identify(unsigned addr) — it is required"], {}, None
    others = [a for m, q, a in bench.active() if a != addr][:say.I2CA_MAX - 1]
    addrs = [addr] + others
    body = "".join(f'    report_int("id_{a:02x}", {fn}({a:#x}));\n' for a in addrs)
    src = f'#include "rt.h"\n#include "{name}.h"\nvoid op_main(void) {{\n{body}}}\n'
    allow = {"bus": "i2c1", "devices": [{"addr": a, "read": True, "pointer": True, "write_regs": [], "max_write": 1} for a in addrs]}
    stage = os.path.join(stage_dir(), name); os.makedirs(stage, exist_ok=True)
    open(os.path.join(stage, f"{name}.h"), "w").write(header)
    cflags = say.CFLAGS
    say.CFLAGS = cflags[:-4] + ["-I", stage] + cflags[-4:]
    try:
        try:
            data, obj, img = say.build(src, f"inc-{name}-identify-" + hashlib.sha1(src.encode() + header.encode()).hexdigest()[:8], allow=allow)
        except RuntimeError as e:
            return False, [f"{fn} does not build: {str(e)[-600:]}"], {}, None
        b = bench.boot(img)
    finally:
        say.CFLAGS = cflags
    res = b.get("result") or {}
    got = {f"0x{a:02x}": res.get(f"id_{a:02x}") for a in addrs}
    rec = {"fn": fn, "at": got, "error": res.get("error")}
    if res.get("error"):
        return False, [f"{fn}: the node stopped it: {res['error']} (an identify may only read)"], rec, None
    me = got[f"0x{addr:02x}"]
    wrong = [a for a, v in got.items() if a != f"0x{addr:02x}" and v == 1]
    if me == 1 and not wrong:
        return True, [], rec, "id"
    if me is not None and me < 0 and not wrong and "-1" in json.dumps([f.get("doc", "") for f in meta["functions"] if fn in f.get("sig", "")]):
        return True, [], rec, "none"                          # declared: nothing readable to recognise it by
    probs = []
    if me != 1: probs.append(f"{fn}(0x{addr:02x}) returned {me!r} at the part's own address (must be 1)")
    if wrong: probs.append(f"{fn} also returned 1 at {', '.join(wrong)} — other parts on the bench, not this one")
    return False, probs, rec, None

def incubate_one(d, calls, rounds=4):
    """Round 1: claude -p with the identification record. If the hub's tests fail, later rounds run Claude Code
    as an agent with the bench tool (probe.py) so it can try the part itself — trial and error, like a person."""
    addr = int(str(d["addr"]), 16)
    qid = next((q for m, q, a in bench.active() if a == addr), None)
    knobs = knobs_of(qid) if qid else {}
    base = fragment_prompt(d, addr, knobs, calls)
    prompt, history, last = base, [], ""
    for rnd in range(1, rounds + 1):
        t0 = time.time()
        if rnd == 1:
            text = claude_cli.ask(FRAG_SYSTEM, prompt)
        else:
            os.environ["POKE_PROBE_TARGETS"] = f"0x{addr:02x}"
            out = claude_cli.agent(prompt, [f"Bash({PY} probe.py:*)"], system=FRAG_SYSTEM + BENCH_TOOL.format(py=PY), cwd=HERE, timeout=2400)
            text = out.get("result", "")
        try:
            meta, header, ops = parse_answer(text)
            name = re.sub(r"[^a-z0-9_]", "", meta["name"].lower())
            if not name or name in json.load(open(CAT)): raise ValueError(f"name {meta['name']!r} is empty or already in the catalog")
            if not meta.get("tests"): raise ValueError("no tests")
        except (ValueError, KeyError) as e:
            prompt = base + f"\n\nYour previous answer could not be used: {e}. Answer in the required format."
            history.append({"round": rnd, "error": str(e)}); continue
        last = text
        log(f"[fragment] 0x{addr:02x} round {rnd}{' (agent + bench)' if rnd > 1 else ''}: {name} — {len(meta['functions'])} functions, {len(meta['tests'])} tests ({time.time() - t0:.0f} s)")
        emit("fragment", state="written", addr=f"0x{addr:02x}", round=rnd, name=name, functions=[f.get("sig") for f in meta["functions"]], seconds=round(time.time() - t0))
        results, problems = [], []
        for i, (test, op) in enumerate(zip(meta["tests"], ops)):
            try:
                ok, why, recs = run_test(name, meta, header, op, test, addr, seed=1000 * rnd + i, knob_now=knobs)
            except Exception as e:                                   # a broken test is a failed test, not a crash
                ok, why, recs = False, [f"test {test.get('name')!r} could not run: {type(e).__name__}: {e}"], []
            results.append({"test": test.get("name"), "ok": ok, "draws": recs, "problems": why})
            problems += why
            log(f"   test {test.get('name')!r}: {'PASS' if ok else 'FAIL'}" + ("" if ok else f" — {why[0][:160]}"))
        ok_id, why_id, rec_id, kind_id = run_identify(name, header, meta, addr)
        results.append({"test": "identify (hub)", "ok": ok_id, "draws": [rec_id], "problems": why_id})
        problems += why_id
        log(f"   identify: {'PASS' if ok_id else 'FAIL'} {rec_id.get('at')}" + ("" if ok_id else f" — {why_id[0][:160]}"))
        if ok_id:
            meta["_identify"] = rec_id["fn"] if kind_id == "id" else None
            meta["_identify_kind"] = kind_id
        history.append({"round": rnd, "name": name, "results": results})
        emit("verify", state="pass" if not problems else "fail", addr=f"0x{addr:02x}", round=rnd,
             tests=[{"test": r["test"], "ok": r["ok"]} for r in results], problems=[p[:300] for p in problems[:4]])
        if not problems and not knobs:                       # nothing on the twin to set: a second, independent reading
            ok2, detail = oracle_check(d, addr, meta, results)
            history[-1]["oracle"] = detail
            log(f"   independent reading: {'agrees' if ok2 else ('cannot tell' if ok2 is None else 'DISAGREES')}"
                + "".join(f" · {c['key']} {c['fragment']} vs {c['independent']}" for c in detail.get("compare", [])))
            if ok2 is False:
                problems = ["an independent reading of the part's registers (a second engineer with the datasheet, who never saw your "
                            "fragment) disagrees: " + json.dumps(detail.get("compare"), ensure_ascii=False) +
                            f"\nRegisters 0x00..0x3f right after power-on: {detail.get('dump')}"]
            else:
                meta["_verification"] = "plausible ranges + independent reading" if ok2 else "plausible ranges only (the independent reading could not tell)"
        if not problems:
            return register(d, addr, name, meta, header, ops, results, history, knobs)
        prompt = (base + "\n\nYour previous answer (fragment and tests):\n" + last[-12000:] +
                  "\n\nThe hub ran those tests on the twin. Problems:\n- " + "\n- ".join(p[:2600] for p in problems[:6]) +
                  "\n\nFind out on the bench what the part really does, fix the fragment (or a test that misreads the part — "
                  "the bench is the truth), and answer again with all blocks.")
    say.write_json_atomic(os.path.join(stage_dir(), f"failed-0x{addr:02x}.json"), {"addr": f"0x{addr:02x}", "verdict": d, "verified": False, "history": history},
                          indent=1, ensure_ascii=False)
    log(f"[fragment] 0x{addr:02x}: not verified after {rounds} rounds — not registered")
    # what the hub decided NOT to remember, and why — so it does not pretend next time
    os.makedirs(os.path.join(INC, "failed"), exist_ok=True)
    say.write_json_atomic(os.path.join(INC, "failed", f"0x{addr:02x}.json"),
                          {"addr": f"0x{addr:02x}", "part": d.get("part"), "identity": d.get("identity"), "confidence": d.get("confidence"),
                           "when": time.strftime("%Y-%m-%d %H:%M"), "rounds": rounds, "verified": False,
                           "last_problems": (history[-1].get("results") if history else None), "bench": bench.name()},
                          indent=1, ensure_ascii=False, default=str)
    emit("failed", addr=f"0x{addr:02x}", why="not verified after %d rounds" % rounds)
    return None

def register(d, addr, name, meta, header, ops, results, history, knobs):
    """The hub decides to remember: fragment, catalog entry, manifest functions, and the record — after the
    verify team's safety review (say.incubation_review, decision 5): least privilege from what the
    verification runs were SEEN writing, write functions denied below the identification threshold."""
    os.makedirs(INC, exist_ok=True)
    when = time.strftime("%Y-%m-%d %H:%M")
    rec = {"name": name, "addr": f"0x{addr:02x}", "part": d.get("part"), "identity": d.get("identity"),
           "fragment_part": name if name.lower() not in str(d.get("part", "")).lower() else None,   # the fragment stage may correct it
           "confidence": d.get("confidence"), "evidence": d.get("evidence"), "registers": d.get("registers"),
           "learned": when, "by": "Claude Code CLI (claude -p / agent with probe.py only)", "bench": bench.name(),
           "functions": meta["functions"], "tests": meta["tests"], "test_ops": ops, "verification": results,
           "rounds": len(history), "twin_knobs": list(knobs), "verified": True,
           "verification_kind": meta.get("_verification") or "twin knobs / screen, two hub draws per test",
           "identify": meta.get("_identify"), "identify_kind": meta.get("_identify_kind"),
           "oracle": history[-1].get("oracle") if history else None,
           "kept_on_node": "nothing — the fragment lives on the hub and joins an image only when a sentence needs it"}
    emit("review", state="start", name=name)
    rv = say.incubation_review(rec)                           # V7
    reviewed = {re.search(r"\b(\w+)\s*\(", f["sig"]).group(1): f for f in rv.get("functions", [])}
    rec["declared_functions"] = meta["functions"]
    rec["functions"] = [reviewed.get(re.search(r"\b(\w+)\s*\(", f["sig"]).group(1), f) for f in meta["functions"]]
    rec["review"] = rv.get("rows"); rec["review_problems"] = rv.get("problems")
    emit("review", state="done", rows=rv.get("rows"), problems=rv.get("problems"))
    log(f"[review] {name}: " + "; ".join(f"{r.get('fn', r.get('function', '?'))}: {r.get('policy', '')} {r.get('why', '')}".strip() for r in (rv.get("rows") or []))[:400])
    sigs = [f"{f['sig']}  /* {f.get('doc', '')} */" for f in rec["functions"]]
    # H8: one writer at a time (key_lock across processes), every shared file replaced whole (write_json_atomic);
    # order: header → manifest functions → catalog, so no reader ever sees a catalog entry without its header.
    with say.key_lock("hub-catalog"):
        cat = json.load(open(CAT))
        if name in cat:
            raise RuntimeError(f"{name} is already in the catalog — incubation adds, never replaces")
        pend = os.path.join(INC, "pending"); os.makedirs(pend, exist_ok=True)   # removed only when the record is written
        say.write_json_atomic(os.path.join(pend, f"{name}.json"), {"name": name, "addr": f"0x{addr:02x}", "since": time.time(), "pid": os.getpid()})
        hp = os.path.join(say.FRAGS, f"{name}.h"); tmp = f"{hp}.{os.getpid()}.tmp"
        open(tmp, "w").write(header.rstrip() + "\n"); os.replace(tmp, hp)
        fns = json.load(open(FN_JSON)) if os.path.exists(FN_JSON) else {"FN": {}, "POLICY": {}}
        for f in rec["functions"]:
            fn = re.search(r"\b(\w+)\s*\(", f["sig"]).group(1)
            fns["FN"][fn] = {"frag": name, "dev": meta.get("dev_ko", name), "addr": addr, "access": f.get("access", "read"),
                             "write_regs": sorted(f.get("write_regs", [])), "max_write": int(f.get("max_write", 1))}
            fns["POLICY"][fn] = f.get("policy") or ("confirm" if f.get("access") == "write" else "allow")
        say.write_json_atomic(FN_JSON, fns, indent=1, ensure_ascii=False)
        cat[name] = {"name": name, "use_when": meta.get("use_when", ""), "functions": sigs, "header": f"{name}.h",
                     "default_addr": f"0x{addr:02x}", "from_driver": f"incubate {when}", "builds": True, "build_error": "",
                     "bench": [bench.name()], "incubated": f"sentence/incubated/{name}.json",
                     **({"identify": meta["_identify"]} if meta.get("_identify") else {})}
        say.write_json_atomic(CAT, cat, indent=2, ensure_ascii=False)
    say.write_json_atomic(os.path.join(INC, f"{name}.json"), rec, indent=1, ensure_ascii=False)
    try: os.remove(os.path.join(INC, "pending", f"{name}.json"))
    except OSError: pass
    log(f"[register] {name} at 0x{addr:02x}: frags/{name}.h, catalog (learned on bench {bench.name()}), {len(rec['functions'])} manifest functions, record incubated/{name}.json")
    emit("register", state="done", name=name, addr=f"0x{addr:02x}")
    return name

AMEND_SYSTEM = """You add ONE function to an existing, verified POKE fragment (a header-only C driver for an I2C part): 
`static int <name>_identify(unsigned addr)` — 1 if the part at addr is this part, 0 if not, negative if it cannot tell.
Use only reads (a register-pointer write is fine): a manufacturer / device ID register, a fixed revision code, a reset
value no other common part has. If the part has nothing readable, return -1 and say so in the doc.
Keep every existing function, table and line exactly as it is; only add the new function (and constants it needs).
Answer with exactly two fenced blocks: ```json {"sig": "static int <name>_identify(unsigned addr)", "doc": "<one line>"} ```
then ```c the whole new header ```."""

def amend_identify(name, rounds=3):
    """H9 for a part registered before identify() was required: the model adds it (with the bench, as an agent,
    after a first plain try), the hub re-runs the part's recorded tests and its identify test, then replaces
    the header and adds the function — one writer, whole-file replaces."""
    rec = json.load(open(os.path.join(INC, name + ".json")))
    hdr0 = open(os.path.join(say.FRAGS, name + ".h")).read()
    addr = int(rec["addr"], 16)
    prompt = (f"Part: {rec.get('part')} — {rec.get('identity')}\nAddress on the bench: {rec['addr']}\n"
              f"Evidence from its identification: {rec.get('evidence')}\nRegisters: {rec.get('registers')}\n\nThe fragment:\n{hdr0}")
    for rnd in range(1, rounds + 1):
        if rnd == 1:
            text = claude_cli.ask(AMEND_SYSTEM, prompt)
        else:
            os.environ["POKE_PROBE_TARGETS"] = f"0x{addr:02x}"
            text = claude_cli.agent(prompt, [f"Bash({PY} probe.py:*)"], system=AMEND_SYSTEM + BENCH_TOOL.format(py=PY), cwd=HERE, timeout=1800).get("result", "")
        js = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S); cs = re.findall(r"```c\s*\n(.*?)```", text, re.S)
        if not js or not cs:
            prompt += "\n\nYour answer had no ```json and ```c blocks. Answer again."; continue
        fdecl = {**json.loads(js.group(1)), "access": "read", "write_regs": [], "max_write": 1, "policy": "allow"}
        header = cs[0]
        meta = {"functions": rec["functions"] + [fdecl]}
        ok, why, rid, kind = run_identify(name, header, meta, addr)
        reg = []
        for i, (t, op) in enumerate(zip(rec["tests"], rec["test_ops"])):          # nothing else may change
            good, w, _ = run_test(name, {"functions": rec["functions"]}, header, op, t, addr, seed=7000 + i,
                                  knob_now={k: {"now": "high"} for k in rec.get("twin_knobs", []) if k.startswith("pin")})
            reg.append({"test": t.get("name"), "ok": good}); why += w
        log(f"[amend] {name} round {rnd}: identify {rid.get('at')} → {'PASS' if ok else 'FAIL'}; recorded tests {sum(r['ok'] for r in reg)}/{len(reg)}")
        if not why:
            with say.key_lock("hub-catalog"):
                hp = os.path.join(say.FRAGS, name + ".h"); tmp = f"{hp}.{os.getpid()}.tmp"
                open(tmp, "w").write(header.rstrip() + "\n"); os.replace(tmp, hp)
                fns = json.load(open(FN_JSON)); fn = rid["fn"]
                fns["FN"][fn] = {"frag": name, "dev": next((v["dev"] for v in fns["FN"].values() if v["frag"] == name), name),
                                 "addr": addr, "access": "read", "write_regs": [], "max_write": 1}
                fns["POLICY"][fn] = "allow"
                say.write_json_atomic(FN_JSON, fns, indent=1, ensure_ascii=False)
                cat = json.load(open(CAT))
                e = cat[name]
                sig = f"{fdecl['sig']}  /* {fdecl.get('doc', '')} */"
                if not any(fn in x for x in e["functions"]): e["functions"] = e["functions"] + [sig]
                if kind == "id": e["identify"] = fn
                say.write_json_atomic(CAT, cat, indent=2, ensure_ascii=False)
            rec.update({"functions": meta["functions"], "identify": fn if kind == "id" else None, "identify_kind": kind,
                        "identify_test": rid, "amended": time.strftime("%Y-%m-%d %H:%M"), "amend_rounds": rnd})
            say.write_json_atomic(os.path.join(INC, name + ".json"), rec, indent=1, ensure_ascii=False)
            log(f"[amend] {name}: {fn} added ({kind}); header replaced, catalog and manifest functions updated")
            return kind
        prompt += ("\n\nThe hub ran it. Problems:\n- " + "\n- ".join(x[:800] for x in why[:6]) +
                   "\nCheck on the bench what really tells this part apart, and answer again.")
    log(f"[amend] {name}: identify not added after {rounds} rounds")
    return None

def reconcile_orphans():
    """A registration cut off midway (process killed, job cancelled) can leave a header and manifest functions
    with no catalog entry, or a catalog entry with no record. At the start of every run: for each pending
    marker, keep the part if it is complete (catalog entry + record), else take back what was written."""
    pend = os.path.join(INC, "pending")
    if not os.path.isdir(pend):
        return []
    done = []
    with say.key_lock("hub-catalog"):
        for f in os.listdir(pend):
            try: m = json.load(open(os.path.join(pend, f)))
            except (OSError, ValueError): m = {"name": f[:-5]}
            name = m.get("name")
            pid = m.get("pid")
            if pid and pid != os.getpid():
                try: os.kill(pid, 0); continue                  # its registration is still running
                except OSError: pass
            cat = json.load(open(CAT))
            complete = name in cat and os.path.exists(os.path.join(INC, f"{name}.json"))
            if not complete:
                if name in cat:
                    cat.pop(name); say.write_json_atomic(CAT, cat, indent=2, ensure_ascii=False)
                fns = json.load(open(FN_JSON)) if os.path.exists(FN_JSON) else {"FN": {}, "POLICY": {}}
                gone = [k for k, v in fns["FN"].items() if v.get("frag") == name]
                for k in gone: fns["FN"].pop(k); fns["POLICY"].pop(k, None)
                if gone: say.write_json_atomic(FN_JSON, fns, indent=1, ensure_ascii=False)
                hp = os.path.join(say.FRAGS, f"{name}.h")
                if os.path.exists(hp): os.remove(hp)
                log(f"[reconcile] took back a half-registered {name}: header, {len(gone)} manifest functions" + (", catalog entry" if name in cat else ""))
                done.append(name)
            os.remove(os.path.join(pend, f))
    return done

def main():
    a = sys.argv[1:]
    reconcile_orphans()
    if a and a[0] == "amend":
        for n in a[1:]: amend_identify(n)
        return
    if not a or a[0] not in ("discover", "run", "verify"):
        print(__doc__); sys.exit(2)
    if a[0] == "discover":
        print(json.dumps(discover(), indent=1)); return
    if a[0] == "verify":
        rec = json.load(open(os.path.join(INC, a[1] + ".json")))
        hdr = open(os.path.join(say.FRAGS, a[1] + ".h")).read()
        meta = {"functions": rec["functions"]}
        ok = True
        for i, (t, op) in enumerate(zip(rec["tests"], rec["test_ops"])):
            good, why, _ = run_test(a[1], meta, hdr, op, t, int(rec["addr"], 16), seed=int(time.time()) + i,
                                    knob_now={k: {"now": "high"} for k in rec.get("twin_knobs", []) if k.startswith("pin")})
            print(t["name"], "PASS" if good else "FAIL", *why[:2]); ok &= good
        sys.exit(0 if ok else 1)
    emit("queued", stage_dir=stage_dir())                    # waiting for the learning run before it, if any
    lock = say.key_lock("learn-job", timeout_s=7200); lock.__enter__()   # one learning run at a time (one bus, one twin bench)
    emit("discover", state="start")
    d = discover()
    emit("discover", state="done", answering=[f"0x{x:02x}" for x in d["answering"]], unknown=[f"0x{x:02x}" for x in d["unknown"]], known=d["known"])
    log(f"[discover] {d['power_ons']} power-ons: answering {', '.join(f'0x{x:02x}' for x in d['answering'])}; known {d['known']}")
    asked = [int(x, 16) for x in a[1:] if not x.startswith("--")]
    known = known_addrs()
    already = [x for x in asked if x in known]
    if already and "--relearn" not in sys.argv:
        names = ", ".join(f"0x{x:02x} ({known[x]})" for x in already)
        log(f"[discover] already known: {names} — nothing to learn (pass --relearn to learn it again)")
        asked = [x for x in asked if x not in known]
        if not asked:
            emit("done", registered=[], why=f"already known: {names}", already=[f"0x{x:02x}" for x in already]); return
    unknown = asked or d["unknown"]
    if not unknown:
        log("[discover] nothing unknown on this bench"); emit("done", registered=[], why="nothing unknown"); return
    log(f"[discover] unknown: {', '.join(f'0x{x:02x}' for x in unknown)}")
    if "--no-map" not in sys.argv:                           # what the map already knows is not learned again
        try:
            import knowledge
            if knowledge.available():
                emit("map", state="start")
                found, how = knowledge.recognise(unknown, log=log)
                if found:
                    w = say.wiring(); tbl = w.get("wiring", w) if isinstance(w, dict) else {}
                    for a, n in found.items():
                        prev = [x for x in tbl.get(n, []) if x != f"0x{a:02x}"]
                        tbl[n] = sorted(set(prev + [f"0x{a:02x}"]))
                        log(f"[map] 0x{a:02x} is {n} — known from the map: wired, not learned again (0 model calls)")
                    say.write_json_atomic(say.WIRING, {"wiring": tbl} if "wiring" in w else tbl, indent=1)
                emit("map", state="done", recognised={f"0x{a:02x}": n for a, n in found.items()}, candidates=how["candidates"])
                unknown = [a for a in unknown if a not in found]
                if not unknown:
                    emit("done", registered=[], recognised={f"0x{a:02x}": n for a, n in found.items()}); return
        except Exception as e:
            log(f"[map] not used: {e}")
    prev = os.path.join(stage_dir(), "identify.json")
    if "--reuse-identify" in sys.argv and os.path.exists(prev):
        idn = json.load(open(prev)); log(f"[identify] reusing {prev} ({idn['power_ons']} power-ons)")
    else:
        idn = identify(unknown)
    done = []
    for v in idn["verdict"]:
        try: addr = int(str(v["addr"]), 16)
        except (KeyError, ValueError): continue
        if addr in unknown:
            n = incubate_one(v, idn["calls"])
            if n: done.append(n)
    st = {**{k: v for k, v in STATS.items() if k != "t0"}, "seconds": round(time.time() - STATS["t0"]),
          "registered": done, "asked": [f"0x{x:02x}" for x in unknown], "when": time.strftime("%Y-%m-%d %H:%M")}
    st["power_ons"] += idn.get("power_ons", 0) if "--reuse-identify" not in sys.argv else 0   # the agent's probe.py runs are separate processes
    say.write_json_atomic(os.path.join(stage_dir(), f"run-{time.strftime('%Y%m%d-%H%M%S')}.json"), st, indent=1)
    if done and "--no-map" not in sys.argv:                # the next call finds them on the map
        try:
            import knowledge
            log(f"[map] {'republished' if knowledge.regenerate('incubation: ' + ', '.join(done)) else 'NOT republished'} with {', '.join(done)}")
        except Exception as e:
            log(f"[map] not republished: {e}")
    emit("done", registered=done, cost=st)
    log(f"[done] registered: {done or 'nothing'} — {st['seconds']} s, {st['model_calls']} model calls "
        f"({st['agent_calls']} as agents), {st['power_ons']} power-ons (scan + identification + tests)")

if __name__ == "__main__":
    main()
