#!/usr/bin/env python3
"""poke say — one sentence, one boot, then the node is off.

    say.py "온도 알려줘"                # sentence → op → image → verify → boot → result → node off
    say.py --show "…"                   # also print the op source
    say.py selftest                     # each fragment against the bench's ground truth
    say.py --pi "…"                     # a real Pi 4: netboot via netboot/tftpd.py, report over its UART

There is no operating system on the node and nothing of ours waits there
between sentences. The hub assembles a complete boot image for the sentence:
the runtime (edge/sentence: entry, UART, result line, power-off), the driver
fragments the sentence needs (edge/sentence/frags — drivers live on the hub),
and the op Claude Code wrote for the sentence. The image is checked before it
is allowed to boot, the node boots it, reports, and powers itself off.

Target: the QEMU twin (raspi4b) with a virtual bench on I2C bus 1. A real Pi 4
boots the same image over the network (see netboot/).
"""
import os, re, sys, json, time, socket, hashlib, subprocess, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EDGE = os.path.join(ROOT, "edge", "sentence")
FRAGS = os.environ.get("POKE_FRAGS") or os.path.join(EDGE, "frags")   # POKE_FRAGS: an isolated copy (실기기팀 5차 신뢰도 실행)
BUILD = os.path.join(HERE, "build")
sys.path.insert(0, os.path.join(ROOT, "sim", "body"))

CC = "aarch64-elf-gcc"
CFLAGS = ["-ffreestanding", "-nostdlib", "-fno-builtin", "-fno-stack-protector", "-O2", "-Wall",
          "-mcpu=cortex-a72", "-mstrict-align", "-mgeneral-regs-only", "-fno-jump-tables", "-I", EDGE, "-I", FRAGS]

# The virtual bench: what is wired to I2C bus 1 on the twin (and its QOM ids for ground truth).
# the twin's body (실기기팀 G1, sentence/bench.py): the default bench unless POKE_BENCH=ext adds the parts
# the hub has not met yet (TMP421, PCA9554, SSD0303). Tests may still replace say.BENCH in their process.
import bench as _bench
BENCH = _bench.active()

# ── build ───────────────────────────────────────────────────────────────
def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[-1500:])
    return r.stdout

# Runtime v1: the runtime runs at EL1 and fences the op at EL0 (edge/sentence/pt.h). image.ld places
# runtime objects by name (poke_rt_*.o); everything else — the op and ustub.o, the op's side of rt.h —
# lands in the op's pages. Extra runtime pieces (e.g. rt_power.c) join RUNTIME_SRC as poke_rt_<name>.o.
RUNTIME_SRC = [("boot.S", "poke_rt_boot.o"), ("rt.c", "poke_rt_main.o"), ("rt_power.c", "poke_rt_power.o"), ("ustub.S", "ustub.o")]
RUNTIME_DEPS = ("rt.h", "pt.h", "rt_power.h", "image.ld")
RUNTIME_CFLAGS = [*CFLAGS, "-Os"]                    # R1: the runtime is built for size (its loops wait on the bus or the clock)

def runtime_objs():
    os.makedirs(os.path.join(BUILD, "rt"), exist_ok=True)
    out = []
    for src, name in RUNTIME_SRC:
        o = os.path.join(BUILD, "rt", name)
        s = os.path.join(EDGE, src)
        newest = max(os.path.getmtime(x) for x in [s, *(os.path.join(EDGE, d) for d in RUNTIME_DEPS)])
        if not os.path.exists(o) or os.path.getmtime(o) < newest:
            tmp = f"{o}.{os.getpid()}.tmp"                    # teams build side by side: never expose a half-written object
            sh([CC, *RUNTIME_CFLAGS, "-c", s, "-o", tmp]); os.replace(tmp, o)
        out.append(o)
    return out

def fence_tool():
    """The off-target fence test (edge/sentence/pt_test.c), built once for the hub's own CPU."""
    t = os.path.join(BUILD, "rt", "pt_test")
    src = os.path.join(EDGE, "pt_test.c")
    if not os.path.exists(t) or os.path.getmtime(t) < max(os.path.getmtime(src), os.path.getmtime(os.path.join(EDGE, "pt.h"))):
        os.makedirs(os.path.dirname(t), exist_ok=True)
        tmp = f"{t}.{os.getpid()}.tmp"
        sh(["cc", "-O1", "-o", tmp, src]); os.replace(tmp, t)
    return t

def fence_check(elf):
    """Walk the page table the runtime will build for this image's layout. Raises if any rule fails."""
    nm = sh(["aarch64-elf-nm", elf])
    r = subprocess.run([fence_tool()], input=nm, capture_output=True, text=True)
    if r.returncode:
        bad = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
        raise RuntimeError("fence check failed for this image layout: " + "; ".join(bad)[:600])
    return r.stdout.strip().splitlines()[-1]

def build(op_src, tag, allow=None):
    """op C source → (image bytes, op object path, image path). The image is linked so that every
    permission boundary is page aligned, and its fence is checked off-target before it may boot.
    allow: the hub's own I2C allow list for an image it wrote itself (probe / scan images, R3) instead of
    the one derived from the op's MANIFEST or fragments — never from a model."""
    try:
        import buildgc; buildgc.maybe()                          # at most hourly: drop old built images (records rebuild)
    except Exception: pass
    if "poke_rt_" in tag:
        raise RuntimeError("an op's tag may not look like a runtime object (poke_rt_*)")
    import threading, shutil
    d = os.path.join(BUILD, "img"); os.makedirs(d, exist_ok=True)
    final = {e: os.path.join(d, f"{tag}.{e}") for e in ("c", "o", "i2c.json", "elf", "img")}
    # T6: the same sentence may be built by several threads/processes at once (demo server, regress runs):
    # build in a private directory, then publish each file with an atomic rename — a reader (check, QEMU)
    # only ever sees a whole file, and same-source builds publish identical content.
    tmpd = os.path.join(d, f".build-{os.getpid()}-{threading.get_ident()}-{os.urandom(3).hex()}")
    os.makedirs(tmpd)
    try:
        c, o, elf, img = (os.path.join(tmpd, f"{tag}.{e}") for e in ("c", "o", "elf", "img"))
        open(c, "w").write(op_src)
        sh([CC, *CFLAGS, "-c", c, "-o", o])
        sh(["aarch64-elf-ld", "-T", os.path.join(EDGE, "image.ld"), "--no-warn-rwx-segments", "--orphan-handling=error",
            "-o", elf, *runtime_objs(), o])
        fence_check(elf)
        sh(["aarch64-elf-objcopy", "-O", "binary", elf, img])
        i2c = i2c_allow_for(op_src, c)
        if allow is not None:
            i2c.update(source="given", allowlist=allow, wired={})
        i2c["src_sha1"] = hashlib.sha1(op_src.encode()).hexdigest()      # check() reads this only for this source
        data = patch_i2c_allow(open(img, "rb").read(), i2c["allowlist"])
        open(img, "wb").write(data)
        json.dump(i2c, open(os.path.join(tmpd, f"{tag}.i2c.json"), "w"), indent=1)
        for e in ("c", "o", "i2c.json", "elf", "img"):
            os.replace(os.path.join(tmpd, f"{tag}.{e}"), final[e])
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    return data, final["o"], final["img"]

# ── I2C allow list (V5): what the runtime lets this op do on the bus ─────────
I2CA_MAGIC, I2CA_MAX, I2CA_REGS = 0x41433249, 8, 12

def included_fragments(c_path):
    """Fragment headers the op source includes (directly or not), by name."""
    deps = sh([CC, *CFLAGS, "-MM", c_path]).replace("\\\n", " ").split()
    return sorted({os.path.basename(x)[:-2] for x in deps if os.path.dirname(os.path.abspath(x)) == os.path.abspath(FRAGS) and x.endswith(".h")})

def i2c_allow_for(op_src, c_path):
    """The op's bus allow list: from its MANIFEST (manifest.i2c_allowlist), or — no MANIFEST — from the
    fragments it includes, minus the functions the hub's policy denies. Problems are refusals for check()."""
    import manifest as mf
    frags, problems = included_fragments(c_path), []
    try:
        m = mf.parse(op_src)
    except mf.ManifestError as e:
        m = None; problems.append(str(e))
    if m:
        declared = {mf.FN[u["fn"]]["frag"] for u in m.get("uses", []) if isinstance(u, dict) and u.get("fn") in mf.FN}
        problems += [f"includes {f}.h but its MANIFEST declares no {f} function (declare it or drop the include)"
                     for f in frags if f not in declared]
        pol = mf.policy()                             # a function the policy denies never reaches the bus,
        uses = [u for u in m.get("uses", []) if isinstance(u, dict) and pol.get(u.get("fn"), "deny") != "deny"]
        allow, source = mf.i2c_allowlist({**m, "uses": uses}), "manifest"   # whatever the run's own gate did
        allow = apply_limits(allow, uses)
    else:
        pol = mf.policy()
        fns = [fn for fn, e in mf.FN.items() if e["frag"] in frags and pol.get(fn, "deny") != "deny"]
        allow, source = mf.i2c_allowlist({"uses": [{"fn": fn} for fn in fns]}), "fragments"
        allow = apply_limits(allow, [{"fn": fn} for fn in fns])
    allow, moved = apply_wiring(allow)
    return {"source": source, "fragments": frags, "allowlist": allow, "problems": problems, "wired": moved,
            "fn_addr": {fn: wired_addrs(e["frag"], e["addr"]) for fn, e in mf.FN.items() if e["frag"] in frags}}

def apply_limits(allow, uses):
    """V6: value ranges and write counts from the hub's FN table (never from the model), tightened — only
    tightened — by the MANIFEST. FN entry, optional:
        "ranges": {"<reg>": [lo, hi]}   first data byte of a write to that register (e.g. a servo's duty)
        "max_writes": N                 ≥2-byte writes to the device per RUN, all calls together
        "auto_increment": true          a multi-byte write [r, d1, d2, …] puts dk in register r+k-1 (each must be
                                        writable and in its range). Absent/false: every dk is checked against r's range
    MANIFEST uses entry, optional: "max_writes": n (≤ the FN's). A device gets a cap only when every write
    function that reaches it has one (their sum); a register's range is the widest any function allows."""
    import manifest as mf
    by_addr = {}
    for u in uses:
        e = mf.FN.get(u.get("fn") if isinstance(u, dict) else None)
        if not e or not e.get("write_regs"):
            continue
        by_addr.setdefault(e["addr"], []).append((e, u))
    devs = []
    for d in allow.get("devices", []):
        ws = by_addr.get(d["addr"], [])
        d = dict(d)
        if ws and all(e.get("max_writes") is not None for e, _ in ws):
            d["max_writes"] = sum(min(int(e["max_writes"]), int(u.get("max_writes", e["max_writes"]))) for e, u in ws)
        rng = {}
        for e, _ in ws:
            for r, (lo, hi) in ((int(str(k), 0), v) for k, v in (e.get("ranges") or {}).items()):
                a, b = rng.get(r, (lo, hi)); rng[r] = (min(a, lo), max(b, hi))
        free = {r for e, _ in ws for r in e.get("write_regs", []) if str(r) not in {str(int(str(k), 0)) for k in (e.get("ranges") or {})}}
        for r in free: rng.pop(r, None)              # a function that writes r without a range leaves it open
        if rng: d["ranges"] = {r: list(v) for r, v in rng.items()}
        if ws and all(e.get("auto_increment") is True for e, _ in ws):   # V6b: only when every writer says so
            d["auto_increment"] = True
        devs.append(d)
    return {**allow, "devices": devs}

# ── X3: where each part really is. A bus scan that finds a known part away from its catalog address
# writes it here (sentence/wiring.json, {"<fragment>": [addr, …]}); the allow list then names the real
# address(es) instead of the default. No file, or no entry: the catalog address. ─────────────────────
WIRING = os.environ.get("POKE_WIRING") or os.path.join(HERE, "wiring.json")   # a body's wiring (an experiment may use its own)

def wiring():
    try:
        w = json.load(open(WIRING))
    except (OSError, ValueError):
        return {}
    out = {}
    for frag, addrs in (w.get("wiring", w) if isinstance(w, dict) else {}).items():
        addrs = addrs if isinstance(addrs, list) else [addrs]
        ok = [int(str(a), 0) for a in addrs if 0x08 <= int(str(a), 0) <= 0x77]
        if ok: out[frag] = sorted(set(ok))
    return out

def wired_addrs(frag, default):
    return wiring().get(frag, [default])

def stale_addresses(src):
    """R2: fragment calls in an op that pass a numeric I2C address where this body no longer has that part
    → [(function, address)]. An op that uses POKE_ADDR_<FRAGMENT> is never stale (the hub rewrites the
    constant per build); an old op with a number is stale only for the parts whose wiring moved."""
    import manifest as mf
    code = re.sub(r"/\*.*?\*/|//[^\n]*", " ", src, flags=re.S)
    out = []
    for fn, e in mf.FN.items():
        for m in re.finditer(rf"\b{fn}\s*\(\s*(0[xX][0-9a-fA-F]+|\d+)\b", code):
            a = int(m.group(1), 0)
            if a not in wired_addrs(e["frag"], e["addr"]):
                out.append((fn, a))
    return sorted(set(out))

def apply_wiring(allow):
    """Move each device of an allow list to where its part is wired (one entry per wired address)."""
    import manifest as mf
    w = wiring()
    if not w:
        return allow, {}
    frag_at = {e["addr"]: e["frag"] for e in mf.FN.values()}
    devs, moved = [], {}
    for d in allow.get("devices", []):
        f = frag_at.get(d["addr"])
        if f in w and w[f] != [d["addr"]]:
            moved[f] = w[f]
            devs += [{**d, "addr": a} for a in w[f]]
        else:
            devs.append(d)
    merged = {}
    for d in devs:                                    # two parts wired to one address: merge their rights
        m = merged.setdefault(d["addr"], {**d, "write_regs": set()})
        m["read"] = m["read"] or d["read"]; m["max_write"] = max(m["max_write"], d["max_write"])
        m["write_regs"] |= set(d["write_regs"])
    return {**allow, "devices": [{**d, "write_regs": sorted(d["write_regs"])} for _, d in sorted(merged.items())]}, moved

def write_json_atomic(path, obj, **kw):
    """Shared JSON (catalog.json, incubated/fn.json, records): readers in other threads/processes must never
    see a half-written file. Write a private temp file, then rename over the old one."""
    tmp = f"{path}.{os.getpid()}-{os.urandom(3).hex()}.tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, **kw)
    os.replace(tmp, path)

import contextlib
@contextlib.contextmanager
def key_lock(key, timeout_s=120):
    """R4: one writer per op key across threads AND processes (demo server, regress, schedule). An flock on
    build/locks/<key>.lock; a second caller waits, then usually finds the first one's op in memory and asks
    no model. Gives up waiting after timeout_s (RuntimeError) rather than hang a sentence forever."""
    import fcntl
    d = os.path.join(BUILD, "locks"); os.makedirs(d, exist_ok=True)
    f = open(os.path.join(d, re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:80] + ".lock"), "a+")
    t0 = time.time()
    try:
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except BlockingIOError:
                if time.time() - t0 > timeout_s:
                    raise RuntimeError(f"op key {key} is busy for more than {timeout_s} s")
                time.sleep(0.05)
        yield
    finally:
        try: fcntl.flock(f, fcntl.LOCK_UN)
        except OSError: pass
        f.close()

def scan_allowlist(addrs):
    """R3: a bus scan's allow list — every address may be READ (0 or 1 byte, no register pointer), nothing
    may be written, not even a 1-byte pointer: an unknown part may take a single byte as a command.
    At most I2CA_MAX addresses per image (one boot)."""
    addrs = sorted(set(int(a) for a in addrs))
    if len(addrs) > I2CA_MAX or any(not 0x03 <= a <= 0x77 for a in addrs):
        raise RuntimeError(f"a scan image reads at most {I2CA_MAX} addresses, each in 0x03–0x77")
    return {"bus": "i2c1", "devices": [{"addr": a, "read": True, "pointer": False, "write_regs": [], "max_write": 0} for a in addrs]}

def patch_i2c_allow(img, allow):
    """Write the allow list into the runtime's i2c_allow table (rt.c) inside the image bytes."""
    import struct as _st
    devs = allow.get("devices", [])
    if len(devs) > I2CA_MAX or any(len(d["write_regs"]) > I2CA_REGS for d in devs):
        raise RuntimeError(f"I2C allow list too large for the runtime table ({I2CA_MAX} devices, {I2CA_REGS} registers each)")
    mag = _st.pack("<II", I2CA_MAGIC, 0)
    at = [i for i in range(0, len(img) - 7, 4) if img[i:i + 8] == mag]
    if len(at) != 1:
        raise RuntimeError(f"runtime I2C allow table not found exactly once in the image ({len(at)})")
    # read byte: bit 0 reads, bit 1 writes of 0–1 byte (register pointer). "pointer" defaults to "read"
    # (a known part's pointer write is part of reading it); a scan of unknown parts sets it False.
    flags = lambda d: ((1 if d.get("read") else 0) | (2 if d.get("pointer", d.get("read")) else 0)
                       | (4 if d.get("auto_increment") is True else 0))     # V6b: where multi-byte writes land
    def rng(d, r):                                   # V6: first data byte range for register r ([lo, hi], default 0..255)
        v = (d.get("ranges") or {}).get(r, (d.get("ranges") or {}).get(str(r), (d.get("ranges") or {}).get(hex(r), [0, 255])))
        lo, hi = int(v[0]), int(v[1])
        if not 0 <= lo <= hi <= 255:
            raise RuntimeError(f"bad value range {v} for register {r:#x} of device {d['addr']:#x}")
        return lo, hi
    def dev(d):
        regs = list(d["write_regs"]); rr = [rng(d, r) for r in regs]
        cap = d.get("max_writes")                    # V6: ≥2-byte writes per run (None = no cap)
        cap = 0xFFFF if cap is None else max(0, min(0xFFFE, int(cap)))
        return (_st.pack("<BBBBHH", d["addr"], flags(d), min(255, d.get("max_write", 1)), len(regs), cap, 0)
                + bytes(regs).ljust(I2CA_REGS, b"\0") + bytes(lo for lo, _ in rr).ljust(I2CA_REGS, b"\0")
                + bytes(hi for _, hi in rr).ljust(I2CA_REGS, b"\0") + bytes(4))
    body = b"".join(dev(d) for d in devs)
    blob = _st.pack("<II", I2CA_MAGIC, len(devs)) + body
    return img[:at[0]] + blob + img[at[0] + len(blob):]

# ── image check (v1: a lint in front of the fence) ──────────────────────────
# The fence (runtime v1, edge/sentence/pt.h) is what keeps an op inside its pages and windows; this
# check is the fast, early feedback for the model before anything boots. It refuses what is certainly
# wrong — instructions an op may not use, calls outside the runtime, a load/store through an address
# built from constants outside the op's windows, an op over budget — and only warns about constants
# that merely look like peripheral addresses (D8: -1, 0xFFFFFFFF, small negative tables are data).
RUNTIME_API = {"say", "report_int", "report_fixed", "report_str", "fail", "ms", "delay_ms", "poke_i2c_xfer"}
# Windows an op may touch directly: none since V5 (I2C goes through the runtime, which enforces the
# image's allow list). Must match PT_DEV_PAGES (EL0) in edge/sentence/pt.h.
OP_WINDOWS = []
PERIPH = (0xFC000000, 0x100000000)
DENY = re.compile(r"^(msr|mrs|sys|sysl|smc|hvc|eret|tlbi|at|dc|ic|wfi|wfe|hlt|svc|br|blr|braa|blraa|eretaa|dcps\d)$")
MEMOP = re.compile(r"^(ld|st|prfm)")
BASE = re.compile(r"\[(x\d+)(?:,\s*#(-?(?:0x[0-9a-f]+|\d+)))?")

def _outside(v):
    return PERIPH[0] <= v < PERIPH[1] and not any(b <= v < b + n for b, n, _ in OP_WINDOWS)

def check(obj):
    """The op object must: use no system / privileged / indirect-branch instructions, access no
    constant-built address in peripheral space outside its windows, call nothing but itself and the
    runtime, and stay small. → {"ok", "problems" (refusals), "warnings", "op_bytes"}."""
    problems, warnings = [], []
    dis = sh(["aarch64-elf-objdump", "-d", "-r", "--no-show-raw-insn", obj])
    syms = sh(["aarch64-elf-nm", obj])
    undefined = {l.split()[-1] for l in syms.splitlines() if l.strip().startswith("U ")}
    for u in sorted(undefined - RUNTIME_API):
        problems.append(f"calls '{u}', which is not in the runtime (no libc on the node)")
    regs, warned = {}, set()
    text_bytes = 0
    for line in dis.splitlines():
        m = re.match(r"\s*([0-9a-f]+):\s+(\S+)\s*(.*)", line)
        if not m:
            if re.match(r"^[0-9a-f]+ <", line): regs = {}            # new function
            continue
        text_bytes += 4
        op, args = m.group(2), m.group(3).split("//")[0].strip()
        if DENY.match(op):
            problems.append(f"instruction '{op} {args}' at {m.group(1)} is not allowed in an op")
            continue
        if MEMOP.match(op):                                         # an access: is its base a known constant?
            bm = BASE.search(args)
            if bm and bm.group(1)[1:] in regs:
                v = (regs[bm.group(1)[1:]] + (int(bm.group(2), 0) if bm.group(2) else 0)) & 0xFFFFFFFFFFFFFFFF
                if _outside(v):
                    problems.append(f"accesses peripheral address {v:#x} at +{m.group(1)} — outside the op's windows (use the fragment functions)")
        a = [x.strip() for x in args.split(",")]
        if op in ("mov", "movz") and len(a) >= 2 and a[1].startswith("#"):
            try:
                v = int(a[1][1:], 0)
                regs[a[0][1:]] = v & (0xFFFFFFFF if a[0].startswith("w") else 0xFFFFFFFFFFFFFFFF)
            except ValueError: pass
        elif op == "movk" and len(a) >= 2 and a[1].startswith("#"):
            sh_ = int(a[2].split("#")[1], 0) if len(a) > 2 and "lsl" in a[2] else 0
            r = a[0][1:]
            regs[r] = (regs.get(r, 0) & ~(0xFFFF << sh_)) | (int(a[1][1:], 0) << sh_)
        elif op in ("add", "sub") and len(a) >= 3 and a[2].startswith("#") and a[1][1:] in regs:
            v = int(a[2][1:].split()[0], 0) << (12 if len(a) > 3 and "lsl" in a[3] else 0)
            regs[a[0][1:]] = regs[a[1][1:]] + (v if op == "add" else -v)
        else:
            if a and a[0][:1] in ("x", "w") and op not in ("str", "strb", "strh", "stp", "stur", "sturb", "sturh", "cmp", "cmn", "tst", "cbz", "cbnz", "tbz", "tbnz", "b", "bl"):
                regs.pop(a[0][1:], None)
            if op == "ldp" and len(a) >= 2 and a[1][:1] in ("x", "w"):
                regs.pop(a[1][1:], None)
        for r, v in list(regs.items()):
            if _outside(v) and (r, v) not in warned:
                warned.add((r, v))
                warnings.append(f"builds a value {v:#x} that looks like a peripheral address (register x{r}) at +{m.group(1)}")
    # values kept as data: literal pools in .text, tables in .rodata/.data — warned, never refused:
    # an op that dereferences one is stopped by the fence, and most such words are just numbers (D8)
    import struct as _st
    secs = re.findall(r"^\s*\d+\s+(\.(?:text|rodata|data|sdata)\S*)\s", sh(["aarch64-elf-objdump", "-h", obj]), re.M)
    for sec in secs:
        tmp = f"{obj}.{os.getpid()}-{os.urandom(3).hex()}.sec"      # T6: checks of one object may run at once
        subprocess.run(["aarch64-elf-objcopy", "-O", "binary", f"--only-section={sec}", obj, tmp], capture_output=True)
        try:
            raw = open(tmp, "rb").read(); os.remove(tmp)
        except OSError:
            raw = b""
        for off in range(0, len(raw) - 7, 8):
            v = _st.unpack_from("<Q", raw, off)[0]
            if _outside(v):
                warnings.append(f"keeps a value {v:#x} in {sec} (+{off:#x}) that looks like a peripheral address")
    if text_bytes > 16384:
        problems.append(f"op code is {text_bytes} B, over the 16 KB budget")
    i2c = None
    side, src = os.path.splitext(obj)[0] + ".i2c.json", os.path.splitext(obj)[0] + ".c"   # written by build()
    try:
        i2c = json.load(open(side))
        if i2c.get("src_sha1") != hashlib.sha1(open(src).read().encode()).hexdigest():
            i2c = None                                              # left by another source under the same tag
    except (OSError, ValueError):
        i2c = None
    if i2c:
        problems += i2c.get("problems", [])
    return {"ok": not problems, "problems": sorted(set(problems)), "warnings": sorted(set(warnings))[:20], "op_bytes": text_bytes, "i2c": i2c}

# ── X1: what the node really did — the runtime's POKE-EFFECTS line, held against the op's declarations ──
def parse_effects(lines):
    """The node's effects line (written by the runtime, never by the op) → dict, or None. Never raises.
    POKE-EFFECTS2 <hex>: the runtime's fixed records (rt.c, R1) decoded here; POKE-EFFECTS {json}: X1's first form.
    → {"v", "n", "truncated", "other_devices", "dev": [{a, w, r, denied, regs, ptr}], "x": [[ms, "w"|"r", addr, hex, n, status]]}"""
    import struct as _st
    for l in reversed(lines):
        if l.startswith("POKE-EFFECTS2 "):
            try:
                raw = bytes.fromhex(l[len("POKE-EFFECTS2 "):].strip())
                total, other, ndev, nx, mx, ver = _st.unpack_from("<IIBBBB", raw, 0)
                if ver != 2 or len(raw) != 12 + 80 * ndev + 20 * nx:
                    return None
                bits = lambda bb: [i for i in range(256) if bb[i >> 3] >> (i & 7) & 1]
                dev = []
                for k in range(ndev):
                    w, r, den, a, used, regs, ptr = _st.unpack_from("<IIIBB32s32s", raw, 12 + 80 * k)
                    dev.append({"a": a, "w": w, "r": r, "denied": den, "regs": bits(regs), "ptr": bits(ptr)})
                x = []
                for k in range(nx):
                    ms, a, rd, st, nb, n, b8 = _st.unpack_from("<IBBBBI8s", raw, 12 + 80 * ndev + 20 * k)
                    x.append([ms, "r" if rd else "w", a, b8[:nb].hex(), n, st])
                return {"v": 2, "n": total, "truncated": total > nx or other > 0, "other_devices": other, "dev": dev, "x": x}
            except (ValueError, _st.error):
                return None
        if l.startswith("POKE-EFFECTS "):
            try:
                v = json.loads(l[len("POKE-EFFECTS "):])
                return v if isinstance(v, dict) and v.get("v") == 1 else None
            except ValueError:
                return None
    return None

def effects_of(b):
    return b.get("effects") if b.get("effects") is not None else parse_effects(b.get("lines", []))

def _sidecar(img_path):
    side = os.path.splitext(img_path)[0] + ".i2c.json" if img_path else None
    try:
        return json.load(open(side)) if side else None
    except (OSError, ValueError):
        return None

def effects_report(b, img_path):
    """POKE-EFFECTS against the image's allow list and the op's MANIFEST:
      undeclared  writes/reads outside the allow list that reached the bus (must be empty: the runtime refuses them)
      refused     transfers the runtime stopped (the op tried; nothing reached the device)
      actions     writes by functions that change the room (FN access "write"), with the device and registers
      missing     declared writes (MANIFEST uses a write function) that never happened
    ok: True / False, or None when there is no record (old runtime, or the node never reached its report)."""
    import manifest as mf
    fx = effects_of(b)
    if fx is None:
        return {"ok": None, "why": "no POKE-EFFECTS line"}
    side = _sidecar(img_path) or {}
    allow = {d["addr"]: d for d in side.get("allowlist", {}).get("devices", [])}
    fn_addr = side.get("fn_addr", {})
    undeclared, refused, actions = [], [], []
    for d in fx.get("dev", []):
        a, rule = d["a"], allow.get(d["a"])
        if d.get("denied"):
            refused.append({"addr": a, "count": d["denied"]})
        done_w = d.get("w", 0) - d.get("denied", 0)
        if not rule and (d.get("r", 0) or done_w) and d.get("denied", 0) < d.get("r", 0) + d.get("w", 0):
            undeclared.append({"addr": a, "what": "a device not in the allow list"})
        elif rule:
            bad = [r for r in d.get("regs", []) if r not in rule["write_regs"]]
            if bad and done_w:
                undeclared.append({"addr": a, "what": f"writes to registers {bad} not in the allow list"})
        for fn, e in mf.FN.items():
            if e.get("access") == "write" and a in fn_addr.get(fn, [e["addr"]]):
                hit = sorted(set(d.get("regs", [])) & set(e.get("write_regs", [])))
                if hit and done_w:
                    actions.append({"fn": fn, "addr": a, "regs": hit, "dev": e.get("dev")})
    missing = []
    src_path = os.path.splitext(img_path)[0] + ".c" if img_path else None
    try:
        m = mf.parse(open(src_path).read()) if src_path and os.path.exists(src_path) else None
    except (OSError, mf.ManifestError):
        m = None
    for u in (m or {}).get("uses", []):
        fn = u.get("fn") if isinstance(u, dict) else None
        if fn in mf.FN and mf.FN[fn].get("access") == "write" and not any(x["fn"] == fn for x in actions):
            missing.append(fn)
    ok = not undeclared and not missing
    return {"ok": ok, "undeclared": undeclared, "refused": refused, "actions": actions, "missing": missing,
            "transfers": fx.get("n"), "truncated": fx.get("truncated")}

def retry_safe(b, img_path):
    """X2: may the hub run a new op for this sentence without asking? Only if this run changed nothing in
    the room: no write by a room-changing function reached a device (configuration writes of read
    functions — a sensor's resolution, a magnetometer's mode — are idempotent and do not count).
    No record at all → not safe (we cannot know). → (bool, Korean reason)."""
    r = effects_report(b, img_path)
    if r["ok"] is None:
        return False, "노드가 실제로 한 일의 기록이 없어 다시 해도 되는지 알 수 없습니다"
    if r["actions"]:
        what = ", ".join(f"{x['dev'] or x['fn']}(0x{x['addr']:02x})" for x in r["actions"])
        return False, f"멈추기 전에 이미 {what}을(를) 바꿨습니다 — 다시 하면 같은 일이 한 번 더 일어납니다"
    return True, None

# ── standing intents (상시 의도): the verify team's safety caps. The product's watcher (D3) calls these;
# the numbers live here so the hub, the screen and the tests read one source. ─────────────────────────
WATCH = {
    "max_s": 5,                 # one watch boot: the op's own bound (MANIFEST max_s) and the RCFG budget
    "min_period_s": 60,         # at most one boot a minute per watch
    "day_cap": 1440,            # boots per watch per day (= the minimum period), then the watch pauses until tomorrow
    "total_day_cap": 2880,      # boots per day across all watches
    "max_active": 4,            # watches armed at once
    "fault_stop": 2,            # consecutive bad runs (FAULT, no result, not powered off) → stop
    "ttl_h": 24,                # a watch expires; the person re-arms it (sees the summary again)
}

def watch_check(op_src):
    """Before a watch is armed: may this op run unattended? Only a READ-ONLY op qualifies —
      · it has a MANIFEST, every function it uses is a read (FN access "read") and allowed by policy;
      · its MANIFEST max_s ≤ WATCH["max_s"].
    → {"ok", "reasons" (Korean), "allowlist"}: build the watch image with say.build(src, tag, allow=allowlist)
    — reads only, pointer writes and the read functions' own configuration writes allowed, no write function
    at all — so even a wrong op cannot act through the runtime."""
    import manifest as mf
    reasons = []
    try:
        m = mf.parse(op_src)
    except mf.ManifestError as e:
        m = None; reasons.append(f"MANIFEST를 읽을 수 없습니다 ({e})")
    if not m:
        return {"ok": False, "reasons": reasons or ["MANIFEST가 없는 op는 상시 감시로 돌릴 수 없습니다"], "allowlist": None}
    pol = mf.policy()
    for a in mf.access(m):
        if a.get("access") != "read":
            reasons.append(f"{a.get('dev') or a['fn']}를 바꾸는 op는 상시 감시로 돌릴 수 없습니다 — 감시는 읽기만, 행동은 조건이 맞을 때 따로 확인")
        elif pol.get(a["fn"], "deny") != "allow":
            reasons.append(f"{a['fn']}는 정책상 바로 허용된 읽기가 아닙니다")
    if not mf.access(m):
        reasons.append("읽는 장치가 없는 op는 감시할 것이 없습니다")
    if not isinstance(m.get("max_s"), (int, float)) or m["max_s"] > WATCH["max_s"]:
        reasons.append(f"감시 op는 한 번에 {WATCH['max_s']}초 안에 끝나야 합니다 (MANIFEST max_s)")
    uses = [u for u in m.get("uses", []) if isinstance(u, dict) and mf.FN.get(u.get("fn"), {}).get("access") == "read"]
    allow, _ = apply_wiring(mf.i2c_allowlist({**m, "uses": uses}))
    return {"ok": not reasons, "reasons": reasons, "allowlist": allow}

def watch_verdict(b, img_path, bad_in_a_row=0):
    """After each watch boot → ("ok" | "strike" | "stop", reason, bad_in_a_row). Stop at once if anything
    changed the room or the runtime refused a transfer (the op tried to step outside); strike for a
    FAULT / no result / not powered off, and stop at WATCH["fault_stop"] strikes in a row."""
    r = effects_report(b, img_path)
    if r.get("actions"):
        return "stop", "감시 op가 장치를 바꾸는 쓰기를 했습니다 — 있을 수 없는 일이라 감시를 멈춥니다", bad_in_a_row + 1
    if r.get("refused") or r.get("undeclared"):
        return "stop", "감시 op가 허용 밖의 버스 전송을 시도했습니다 — 감시를 멈춥니다", bad_in_a_row + 1
    fault = any(l.startswith("POKE-FAULT") for l in b.get("lines", []))
    if fault or not b.get("powered_off") or not b.get("result") or r.get("ok") is None:
        n = bad_in_a_row + 1
        if n >= WATCH["fault_stop"]:
            return "stop", f"감시 op가 {n}번 연속 실패했습니다 — 감시를 멈춥니다", n
        return "strike", "감시 op가 실패했습니다 (한 번 더 실패하면 멈춤)", n
    return "ok", None, 0

# ── V7: safety review of what incubation learned, before (or after) it is registered ─────────────────
INCUBATION_MIN_CONFIDENCE = 0.8

def incubation_review(rec):
    """rec: an incubation record (incubated/<name>.json shape: addr, confidence, functions[{sig, access,
    write_regs, max_write, policy}], test_ops, verification[{test, draws[{lines|effects}]}]).
    Least privilege from evidence: a function keeps only the write registers its own verification runs were
    SEEN writing (POKE-EFFECTS, attributed to the tests whose op calls it), and a max_write no longer than
    the longest write seen. A model's identification below INCUBATION_MIN_CONFIDENCE leaves write functions
    denied by default. → {"functions": [reviewed copies], "rows": [table rows], "problems": [...]}."""
    addr = int(str(rec.get("addr")), 0)
    conf = float(rec.get("confidence") or 0)
    fname = lambda f: re.search(r"\b(\w+)\s*\(", f["sig"]).group(1)
    names = [fname(f) for f in rec.get("functions", [])]
    tests = {t["name"]: (rec.get("test_ops") or [None] * 99)[i] for i, t in enumerate(rec.get("tests", []))}
    seen = {n: {"regs": set(), "maxw": 0, "draws": 0, "refused": 0} for n in names}
    problems = []
    for v in rec.get("verification", []):
        if v.get("ok") is False:                              # only a run that passed is evidence of what the part needs
            continue
        src = tests.get(v.get("test")) or ""
        callers = [n for n in names if re.search(rf"\b{n}\s*\(", src)]
        for dr in v.get("draws", []):
            fx = dr.get("effects") or parse_effects(dr.get("lines", []))
            if fx is None:
                problems.append(f"{v.get('test')}: a verification run without POKE-EFFECTS — nothing seen, nothing kept")
                continue
            dev = next((d for d in fx.get("dev", []) if d["a"] == addr), None)
            regs = set(dev["regs"]) if dev else set()
            maxw = max([x[4] for x in fx.get("x", []) if x[1] == "w" and x[2] == addr and x[4] >= 2] or [0])
            for n in callers:
                seen[n]["regs"] |= regs; seen[n]["maxw"] = max(seen[n]["maxw"], maxw); seen[n]["draws"] += 1
                seen[n]["refused"] += dev.get("denied", 0) if dev else 0
    out, rows = [], []
    for f, n in zip(rec.get("functions", []), names):
        decl = sorted(f.get("write_regs", []))
        s = seen[n]
        keep = sorted(set(decl) & s["regs"])
        maxw = min(int(f.get("max_write", 1)), max(s["maxw"], 1)) if keep else 1
        policy = f.get("policy") or ("confirm" if f.get("access") == "write" else "allow")
        why = []
        if s["draws"] == 0:
            why.append("no verification run calls it")
            keep, maxw = [], 1
            if f.get("access") == "write": policy = "deny"
        if f.get("access") == "write" and conf < INCUBATION_MIN_CONFIDENCE:
            policy = "deny"; why.append(f"identified at {conf:.0%} < {INCUBATION_MIN_CONFIDENCE:.0%}")
        if set(decl) - set(keep):
            why.append(f"never seen writing {sorted(set(decl) - set(keep))}")
        if s["refused"]:
            why.append(f"{s['refused']} transfers refused during verification")
        out.append({**f, "write_regs": keep, "max_write": maxw, "policy": policy})
        rows.append({"fn": n, "access": f.get("access"), "declared": decl, "seen": sorted(s["regs"]), "kept": keep,
                     "max_write": [int(f.get("max_write", 1)), maxw], "runs": s["draws"],
                     "policy": [f.get("policy") or ("confirm" if f.get("access") == "write" else "allow"), policy], "why": why})
    return {"functions": out, "rows": rows, "problems": problems, "confidence": conf}

def fault_feedback(b):
    """A boot that ended in POKE-FAULT (or never finished) → a sentence for the model's next attempt, or None.
    Only the op's own faults count; a device that did not answer is a normal fail(), not a fault."""
    f = next((l for l in b.get("lines", []) if l.startswith("POKE-FAULT")), None)
    why = (b.get("result") or {}).get("error", "")
    if f:
        detail = " ".join(x for x in f.split()[1:] if not x.startswith(("esr=", "elr=")))
        return f"when it ran on the node it was stopped: {why or 'it faulted'} ({detail}). Fix the op so this cannot happen."
    if not b.get("powered_off") and not b.get("result"):
        return "when it ran on the node it never finished (no result, no power-off). Keep every loop bounded."
    return None

# ── boot on the twin ────────────────────────────────────────────────────
class QMP:
    """QEMU's machine protocol (JSON), for setting the bench before power-on and reading it after power-off."""
    def __init__(self, port):
        for _ in range(200):
            try:
                self.s = socket.create_connection(("127.0.0.1", port), timeout=5); break
            except OSError:
                time.sleep(0.02)
        self.f = self.s.makefile("rwb")
        self._line()                                            # greeting
        self.call("qmp_capabilities")
    def _line(self):
        while True:
            m = json.loads(self.f.readline())
            if "event" not in m:
                return m
    def call(self, cmd, **args):
        self.f.write((json.dumps({"execute": cmd, "arguments": args} if args else {"execute": cmd}) + "\n").encode()); self.f.flush()
        m = self._line()
        if "error" in m:
            raise RuntimeError(f"qmp {cmd}: {m['error'].get('desc')}")
        return m.get("return")
    def set(self, qid, prop, value):
        return self.call("qom-set", path=f"/machine/peripheral/{qid}", property=prop, value=value)
    def get(self, qid, prop):
        return self.call("qom-get", path=f"/machine/peripheral/{qid}", property=prop)

def boot(img_path, bench_state=None, budget_s=30, script=(), check_wipe=True):
    """Boot one image on the twin. Returns the node's lines, the parsed result, timings, and ground truth
    read from the devices after the node powered off."""
    port = 20000 + int.from_bytes(os.urandom(2), "little") % 20000
    devs = []
    for model, qid, addr in BENCH:
        devs += ["-device", f"{model},id={qid},bus=i2c-bus.1,address={addr:#x}"]
    os.makedirs(os.path.join(BUILD, "serial"), exist_ok=True)
    serial = os.path.join(BUILD, "serial", f"{os.getpid()}-{port}.txt"); open(serial, "w").close()  # one file per boot: twins may run side by side
    t_launch = time.time()
    q = subprocess.Popen(["qemu-system-aarch64", "-M", "raspi4b", "-kernel", img_path, "-display", "none", "-S",
                          "-serial", f"file:{serial}", "-qmp", f"tcp:127.0.0.1:{port},server=on,wait=off", "-monitor", "none",
                          "-action", "reboot=shutdown,shutdown=pause", *devs],
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    mon = None
    try:                                                      # whatever happens, the QEMU process does not outlive this call
        mon = QMP(port)
        for k, v in (bench_state or {}).items():
            qid, prop = k.split(); mon.set(qid, prop, v)
        t_power = time.time()
        mon.call("cont")
        lines, off_line, vm_off, wipe = [], False, False, None
        pending = sorted(script)                              # (seconds after power-on, "qid prop", value)
        while time.time() - t_power < budget_s:
            while pending and time.time() - t_power >= pending[0][0]:
                _, k, v = pending.pop(0); qid, prop = k.split(); mon.set(qid, prop, v)
            lines = open(serial, errors="replace").read().replace("\r", "").splitlines()
            if any(l.startswith("POKE-OFF") for l in lines):
                off_line = True; break
            if q.poll() is not None: break
            time.sleep(0.02)
        t_off = time.time()
        for _ in range(50):                                   # let the reset land (≤ 1 s) before asking the VM
            st = mon.call("query-status")
            vm_off = st.get("status") == "shutdown" or not st.get("running")
            if vm_off or not off_line: break
            time.sleep(0.02)
        off = off_line and vm_off                             # the node said so AND the machine agrees
        status = f"{st.get('status')}" + (" (guest powered off)" if vm_off else "")
        truth = {}
        for model, qid, addr in BENCH:
            if model == "pca9552":
                truth["leds_on"] = [i for i in range(16) if mon.get(qid, f"led{i}") == "on"]
            if model == "tmp105":
                truth["temperature_mC"] = mon.get(qid, "temperature")
        if any(m not in ("tmp105", "ds1338", "lsm303dlhc_mag", "pca9552") for m, _, _ in BENCH):
            try: truth["ext"] = _bench.truth(mon, BENCH)        # parts learned by incubation (POKE_BENCH=ext)
            except (OSError, RuntimeError, ValueError, AttributeError) as e: truth["ext_error"] = str(e)[:200]
        wipe = wipe_check(mon, img_path) if (vm_off and check_wipe) else None
    finally:
        if mon:
            try: mon.call("quit")
            except (OSError, ValueError, RuntimeError): pass
        try: q.wait(5)
        except subprocess.TimeoutExpired: q.kill(); q.wait()
        try: lines = open(serial, errors="replace").read().replace("\r", "").splitlines()
        except OSError: lines = []
        try: os.remove(serial)                                # gone on every path, the failed ones too
        except OSError: pass
    res, bad = parse_result(lines)
    end = next((l for l in lines if l.startswith("POKE-END")), "")
    m = re.search(r"ms=(\d+)", end)
    out = {"lines": lines, "result": res, "powered_off": off, "vm_status": status, "truth": truth,
           "node_ms": int(m.group(1)) if m else None,
           "boot_to_off_ms": round((t_off - t_power) * 1000), "qemu_total_ms": round((time.time() - t_launch) * 1000)}
    if bad: out["result_error"] = bad
    out["effects"] = parse_effects(lines)                     # verify team X1: the runtime's record of bus actions
    if off_line and not vm_off: out["warning"] = "a POKE-OFF line appeared but the machine was still running"
    if wipe: out["wipe_check"] = wipe
    return out

def wipe_check(mon, img_path):
    """After the node reset itself, read back the memory our image used beyond its own code — the op's
    pages, the runtime's data and stack: [__rt_text_end, __rt_end). The twin's RAM, read by the hub:
    evidence, not the node's claim. → {"from", "to", "bytes", "nonzero"} or None."""
    elf = img_path[:-4] + ".elf"
    if not os.path.exists(elf):
        return None
    sym = {}
    for l in subprocess.run(["aarch64-elf-nm", elf], capture_output=True, text=True).stdout.splitlines():
        f = l.split()
        if len(f) == 3 and f[2] in ("__rt_text_end", "__rt_end"):
            sym[f[2]] = int(f[0], 16)
    if len(sym) < 2 or sym["__rt_end"] <= sym["__rt_text_end"]:
        return None
    a, b = sym["__rt_text_end"], sym["__rt_end"]
    dump = os.path.join(BUILD, "serial", f"wipe-{os.getpid()}-{os.urandom(4).hex()}.bin")
    try:
        mon.call("pmemsave", val=a, size=b - a, filename=dump)
        raw = open(dump, "rb").read()
    except (OSError, RuntimeError):
        return None
    finally:
        if os.path.exists(dump): os.remove(dump)
    if len(raw) != b - a:
        return None
    return {"from": a, "to": b, "bytes": len(raw), "nonzero": sum(1 for x in raw if x)}

def parse_result(lines):
    """The last POKE-RESULT line the node printed → (dict or None, problem or None). Never raises."""
    raw = [l[len("POKE-RESULT "):] for l in lines if l.startswith("POKE-RESULT ")]
    if not raw:
        return None, None
    try:
        v = json.loads(raw[-1])
        return (v, None) if isinstance(v, dict) else (None, "result is not an object")
    except ValueError as e:
        return None, f"result line is not valid JSON ({e.msg})"

# ── boot on a real Pi 4 (netboot) ───────────────────────────────────────
def serial_lines(dev, until, budget_s):
    """Read ASCII lines from a USB-serial adapter (115200 8N1) until a line starts with `until`."""
    import termios
    fd = os.open(dev, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        a = termios.tcgetattr(fd)
        a[0] = 0; a[1] = 0; a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL; a[3] = 0
        a[4] = a[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, a)
        buf, lines, t0 = b"", [], time.time()
        while time.time() - t0 < budget_s:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                chunk = b""
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    l, buf = buf.split(b"\n", 1)
                    l = l.decode(errors="replace").strip()
                    lines.append(l)
                    if l.startswith(until):
                        return lines, True
            else:
                time.sleep(0.01)
        return lines, False
    finally:
        os.close(fd)

def boot_netboot_twin(img_path, budget_s=60):
    """The netboot path end to end without the Pi: hand the image to the hub's tftpd; the virtual
    bootloader (netboot/vboot.py, running) fetches it over TFTP, boots the twin, and writes a report."""
    out = os.path.join(HERE, "netboot", "vboot-out")
    os.makedirs(out, exist_ok=True)
    before = set(os.listdir(out))
    subprocess.run([sys.executable, os.path.join(HERE, "netboot", "tftpd.py"), "--pend", img_path], check=True, capture_output=True)
    t0 = time.time()
    while time.time() - t0 < budget_s:
        new = [f for f in set(os.listdir(out)) - before if f.endswith(".json")]
        if new:
            time.sleep(0.05)
            b = json.load(open(os.path.join(out, sorted(new)[-1])))
            b["vm_status"] = f"{b.get('vm_status')} · via TFTP in {b.get('fetched_ms')} ms"
            return b
        time.sleep(0.05)
    raise RuntimeError("the virtual bootloader did not boot the image (is netboot/vboot.py running with netboot/tftpd.py?)")

def boot_pi(img_path, serial_dev=None, budget_s=60):
    """Hand the image to the hub's tftpd (netboot/) and read the node's report over its UART.
    The node is waiting in its bootloader; it fetches the image, runs it, powers off (watchdog
    reset) and is back in its bootloader with nothing of ours on it."""
    import glob
    dev = serial_dev or (sorted(glob.glob("/dev/cu.usbserial*") + glob.glob("/dev/cu.usbmodem*")) or [None])[0]
    if not dev:
        raise RuntimeError("no USB-serial adapter found (the node reports on its UART: GPIO 14 TX → adapter RX, GND → GND)")
    # --target pi: the Pi's image settings (watchdog armed at boot, I2C divider for its core clock)
    subprocess.run([sys.executable, os.path.join(HERE, "netboot", "tftpd.py"), "--pend", img_path, "--target", "pi"], check=True, capture_output=True)
    t0 = time.time()
    lines, off = serial_lines(dev, "POKE-OFF", budget_s)
    res, bad = parse_result(lines)
    end = next((l for l in lines if l.startswith("POKE-END")), "")
    return {"lines": lines, "result": res, "powered_off": off, "vm_status": "real Pi 4 (netboot)", "truth": {}, "result_error": bad,
            "effects": parse_effects(lines),
            "node_ms": int(re.search(r"ms=(\d+)", end).group(1)) if end else None,
            "boot_to_off_ms": round((time.time() - t0) * 1000), "qemu_total_ms": None}

# ── the sentence → op ───────────────────────────────────────────────────
def catalog():
    """Fragments this body can use — the parts it really has (실기기팀 H7, sentence/bodycheck.py).
    A part learned by incubation ("incubated"/"bench" in its entry) is listed only when the last read-only
    scan of this body (cached per body; ~1 s the first time) found it where it is wired. A part a body
    check declared gone is left out until the body is scanned again. The original parts are otherwise
    always listed (an absent one is met at boot and answered by the body check), so the default bench's
    catalog — and every cache key made from it — is what it was."""
    cat = json.load(open(os.path.join(FRAGS, "catalog.json")))
    learned = {k for k, v in cat.items() if v.get("incubated") or v.get("bench")}
    try:
        import bodycheck
        pres = bodycheck.present()
    except Exception:                                        # no scan possible (no QEMU …): fall back to bench names
        return {k: v for k, v in cat.items() if k not in learned or _bench.name() in (v.get("bench") or [])}
    here, gone = set(pres.get("present", [])), set(pres.get("gone", []))
    out = {}
    for k, v in cat.items():
        if k in gone:
            continue
        if k in learned:
            try: d = int(str(v.get("default_addr")), 16)
            except (TypeError, ValueError): continue
            if not any(int(str(a), 0) in here for a in wired_addrs(k, d)):
                continue
        out[k] = v
    return out

OP_SYSTEM = """You write the op for ONE sentence on POKE: a bare-metal node with no operating system. The hub boots an image made of a fixed runtime, the driver fragments you include, and your op; the node runs it once, reports, and powers itself off. Output ONE C file and nothing else — no markdown fences.

The file starts with two comment blocks (the hub reads them before anything boots):
/* MANIFEST {"v": 1, "uses": [{"fn": "<fragment function>"}, …], "max_s": <seconds the op runs, ≤ 20>, "summary_ko": "<one plain Korean sentence for the person: what will happen, with {P_NAME} where a parameter goes (it shows the number as spoken, so write the unit after it: {P_GAP_MS}초, {P_TH}도)>", "labels_ko": {"<each result key>": "<Korean label, unit in parentheses, e.g. 온도 (°C); for a coded value the legend, e.g. LED 0 (1=켜짐, 0=꺼짐)>", "<a key the person need not see>": ""}} */
/* PARAMS [{"name": "P_NAME", "num": <index of the number in the sentence, 0-based, counting only numbers written with digits>, "scale": <the #define value = spoken number × scale>, "min": <int>, "max": <int>, "ko": "<Korean name of the value>"}, …] */
- MANIFEST.uses lists every fragment function the op calls, once each. For a function that changes a device add "targets" (channel numbers or parameter names) and "end": "leave".
- Every number written with digits in the sentence (LED 3번, 25도, 0.2초, 12초) becomes a parameter: `#define P_NAME <integer>` used in the code and declared in PARAMS (0.2초 with scale 1000 → `#define P_GAP_MS 200`). min/max = what the hardware and the 20 s limit accept. No digit numbers → `/* PARAMS [] */`.

Instead of a C file you may answer with exactly one line:
- NEED: <a short Korean question ending in ?> — the sentence would change a device but leaves out which one or how (e.g. "불 좀 켜줘": which LED?) and there is no single obvious reading. A reading ("온도 알려줘") never needs a question: report everything relevant.
- CANNOT: <Korean reason> — nothing on this node can do it (no fragment for it).
- LONG: {"each": "<a Korean sentence for ONE short boot>", "every_s": <seconds>, "total_s": <seconds>} — the request takes longer than one boot (over 20 s, e.g. "1분 동안 10초마다 온도"). The hub boots `each` every every_s seconds; do not write the waiting into an op.
- WATCH: {"check": "<a Korean sentence for ONE short read-only boot that reports hit (1 when the condition holds, else 0) and the value it looked at>", "every_s": <seconds between checks, at least 60>, "then": "<a Korean sentence for the action to take when it holds, or \"\" when the person only wants to be told>", "summary_ko": "<one Korean line>"} — ONLY for a standing request: the person wants to be told (or have something done) LATER, whenever a condition becomes true, with no end time — "온도가 30도 넘으면 알려줘", "30도 넘으면 경고해 줘", "지켜보다가 30도 넘으면 LED 0번 켜줘" (then = "LED 0번 켜줘"), "계속 감시하다가 …". A condition with an action and no such wording ("온도가 25도 넘으면 LED 0번 켜줘") is a one-time op that checks now; a sentence that also asks for something now ("온도 알려주고 25도 넘으면 경고해줘") is a one-time op too. The check reads only; it never changes a device.

The op:
- #include "rt.h" and the fragment headers it needs (by their file names). Nothing else.
- Defines `void op_main(void)`; returns when the sentence is done. Keep it short and finite: no unbounded loops (a wait is a bounded loop with delay_ms; total run under 20 s).
- delay_ms(n) takes n ≤ 10000; a larger n stops the op with an error, and the node stops any op at 20 s. Never wait longer on the node: waits beyond one boot are the hub's job (LONG).
- Talks to hardware ONLY through fragment functions. Never touches addresses, registers or peripherals itself.
- Passes each device's I2C address ONLY as the hub constant `POKE_ADDR_<FRAGMENT NAME IN CAPITALS>`, defined once at the top: e.g. `#define POKE_ADDR_TMP105 0x48`, then `tmp105_read_mC(POKE_ADDR_TMP105, &mC)`. The hub sets the value to where the part is wired on this body.
- Reports what the person asked for with report_int / report_fixed / report_str (short snake_case keys; temperatures in °C with a key ending in _c) and narrates in one or two short say() lines in Korean (UTF-8 is fine; each under 40 characters, plain words for the person, e.g. say("온도를 읽었어요.")). report_str values may be Korean too. fail("…") takes a short Korean reason. On a fragment error, call fail("…") and return.
- A sentence that asks whether a condition holds "now" (e.g. a watch check) reports report_int("hit", 1 or 0) plus the value it compared.
- The hardware clock keeps UTC. For the time or date, convert to Korean local time: `#define POKE_UTC_OFFSET_MIN 540` (the hub sets the value), add it, carry into the hour/day/month/year, and report_str("time_kst", "HH:MM:SS"), report_str("date_kst", "YYYY-MM-DD"), report_int("hour", local hour), report_str("tz", "KST").
- No libc, no printf, no memcpy/memset, no function pointers, no inline assembly.

rt.h: void say(const char *s); void report_int(const char *key, long v); void report_fixed(const char *key, long v, int decimals); void report_str(const char *key, const char *s); void fail(const char *why); unsigned long ms(void); void delay_ms(unsigned long n);"""

def op_prompt(sentence, cat, feedback=None):
    wiring = "\n".join(f"- {name}.h at I2C address {', '.join(str(a) if isinstance(a, str) else hex(a) for a in wired_addrs(name, e['default_addr']))} (from the incubated driver {e.get('from_driver', '')})\n"
                       f"  use when: {e.get('use_when', '')}\n  functions:\n" + "\n".join("    " + f for f in e["functions"])
                       for name, e in cat.items())
    nums = __import__("record").numbers(sentence)
    p = (f"The person said: \"{sentence}\"\n(numbers written with digits, by index: {dict(enumerate(nums)) if nums else 'none'})\n\n"
         f"What this node has (fragments the hub can put in the image, and where each device is wired):\n{wiring}")
    if feedback:
        p += f"\n\nYour previous op for this sentence was rejected: {feedback}\nWrite the whole file again."
    return p


class Refused(RuntimeError):
    """The image check refused the op: it must not boot."""

def _legacy_manifest(src):
    """An op written before manifests (build/ops cache): derive what it uses from its calls."""
    import manifest as MF
    fns = sorted(MF.called_functions(src))
    devs = sorted({MF.FN[f]["dev"] for f in fns})
    return {"v": 1, "uses": [{"fn": f} for f in fns], "max_s": 20,
            "summary_ko": (", ".join(devs) + " 사용" if devs else "장치를 쓰지 않음") + " — 이전 형식으로 기억된 op라 자세한 설명이 없습니다"}

def _op_dict(src, tag, **kw):
    img, obj, path = build(src, tag)
    return {"src": src, "img": img, "obj": obj, "path": path, "key": tag, "check": check(obj), **kw}

def _serve_match(sentence, m, judge_s=0):
    """A record judged to mean the same (intent.py): its op with this sentence's numbers; the wording is remembered."""
    import record as REC, hub, messages_ko as KO
    r, params = m["record"], m["params"]
    for d in r.get("params", []):
        v, lo, hi = params.get(d["name"]), d.get("min"), d.get("max")
        if v is None or (lo is not None and v < lo) or (hi is not None and v > hi):
            sc = d.get("scale", 1) or 1
            raise KO.Refusal("range", f"{d.get('ko') or d['name']}: {lo / sc:g}–{hi / sc:g}만 됩니다" if lo is not None and hi is not None
                             else f"{d.get('ko') or d['name']} 값을 읽지 못했습니다", "노드를 켜지 않았습니다.")
    REC.heard(r, sentence)
    rep = _repronounce_if_needed(sentence, r, lambda *a: None, 3)
    if rep:
        rep["matched"] = {"how": m["how"], "base": r["sentences"][0], "why": m.get("why", "")}
        return rep
    src = hub.with_hub_consts(REC.apply_params(r["op"], params))
    tag = r["id"] + ("-" + hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:6] if params else "")
    return _op_dict(src, tag, cached=True, gen_s=judge_s, rounds=0, manifest=r.get("manifest") or {}, record=r, params=params,
                    matched={"how": m["how"], "base": r["sentences"][0], "why": m.get("why", "")})

def _repronounce_if_needed(sentence, r, log, rounds):
    """더늠: the card (sentence, traces) stays; the op is one pronunciation for one body. If the op does not
    fit this body, bring back an earlier pronunciation made for this body, or have the sentence pronounced
    again here. → an op dict (with "repronounced") or None when the op fits."""
    import body, record as REC
    ok, why = body.fits(r.get("op") or "", r.get("manifest"))
    if ok:
        return None
    try:                                                   # a part this op needs is known to be gone: say so, no model
        import bodycheck, manifest as MF
        gone = set((bodycheck.present() or {}).get("gone") or [])
        need = {MF.FN.get(u.get("fn"), {}).get("frag") for u in (r.get("manifest") or {}).get("uses", []) if isinstance(u, dict)}
        if gone & need:
            import messages_ko as KO
            dev = {e["frag"]: e["dev"] for e in MF.FN.values()}
            names = ", ".join(f"{dev.get(f, f)}({f.upper()})" for f in sorted(gone & need))
            raise KO.Refusal("gone", f"이 몸에서 {names}가 빠져 있어 지금은 할 수 없습니다 — 모델을 부르지 않았습니다",
                             "부품을 다시 꽂으면 이 카드 그대로 됩니다.")
    except ImportError:
        pass
    now = body.current(); fp_now = body.fp(now)
    was = ((r.get("colophon") or {}).get("body") or ((r.get("origin") or {}).get("from_body")) or {})
    info = {"from": was.get("label") or "이전 몸", "to": body.label(now), "why": why}
    for e in reversed(r.get("lineage") or []):
        eb = (e.get("colophon") or {}).get("body") or {}
        if eb.get("fp") == fp_now and body.fits(e.get("op") or "", e.get("manifest"))[0]:
            REC.restore(r, e)
            log(f"  (this body has its own pronunciation of {r['sentences'][0]!r} — using it)")
            d = make_op(sentence, log=log, rounds=rounds, _again=True)
            d["repronounced"] = {**info, "how": "lineage"}
            return d
    log(f"  (the body changed — pronouncing {r['sentences'][0]!r} again: {'; '.join(why)})")
    t = time.time()
    fb = ("the body changed since this op was written: " + "; ".join(why) + ". Pronounce the same sentence again for "
          f"this body ({info['to']}); keep the behaviour and the result keys, and pass every device address only as its "
          f"POKE_ADDR_<FRAGMENT> constant. The op that worked on the previous body ({info['from']}):\n{r.get('op')}")
    make_op(r["sentences"][0], log=log, rounds=rounds, feedback=fb)
    d = make_op(sentence, log=log, rounds=rounds, _again=True)
    d["repronounced"] = {**info, "how": "model", "gen_s": round(time.time() - t, 1)}
    return d

import threading as _threading
_OP_HELD = _threading.local()

def make_op(sentence, log=print, rounds=3, feedback=None, _again=False):
    """R4 (decision 3): one maker per sentence at a time — the lookup → write → remember of a sentence runs
    under say.key_lock, so a second caller (another tab, a schedule, another process) waits and then finds
    the first one's record: one model call, one op. Re-entrant within a thread (re-pronouncing calls back)."""
    import record as REC
    key = "op-" + REC._rid(sentence)
    held = getattr(_OP_HELD, "keys", None)
    if held is None:
        held = _OP_HELD.keys = set()
    if key in held:
        return _make_op(sentence, log, rounds, feedback, _again)
    with key_lock(key):
        held.add(key)
        try:
            return _make_op(sentence, log, rounds, feedback, _again)
        finally:
            held.discard(key)

def _make_op(sentence, log=print, rounds=3, feedback=None, _again=False):
    """The hub's memory first (record.py: same sentence, or same wording with other numbers — no model call),
    then the older build/ops cache, then Claude Code writes a new op.
    feedback: why the last op for this sentence failed on the node (fault_feedback) — skips memory and asks
    the model again with that reason; a passing op replaces the remembered one.
    A sentence that is not an op raises messages_ko.Refusal: kind need / cannot / long / range (no boot)."""
    import claude_cli, record as REC, hub, messages_ko as KO, intent
    cat = catalog()
    cat_key = hashlib.sha1(json.dumps(cat, sort_keys=True).encode()).hexdigest()[:8]
    pre = intent.prefilter(sentence)                       # decision 2: refused before any op is written
    if pre:
        raise KO.Refusal(pre[0], pre[1] if pre[0] != "deny" else "허브 정책이 이 요청을 막았습니다: " + pre[1], "노드를 켜지 않았습니다.")
    if not feedback:
        hit = REC.lookup(sentence)
        if hit:
            r = hit.record
            if hit.kind == "range":
                raise KO.Refusal("range", hit.text, "노드를 켜지 않았습니다.")
            REC.heard(r, sentence)                             # a wording that really maps here
            if hit.kind != "op":
                raise KO.Refusal(hit.kind, hit.text, "기억된 답입니다.")
            if not _again:                                     # does the remembered op fit the body we have now?
                rep = _repronounce_if_needed(sentence, r, log, rounds)
                if rep:
                    return rep
            src = hub.with_hub_consts(hit.src)
            tag = r["id"] + ("-" + hashlib.sha1(json.dumps(hit.params, sort_keys=True).encode()).hexdigest()[:6] if hit.params else "")
            try:
                d = _op_dict(src, tag, cached=True, gen_s=0, rounds=0, manifest=r.get("manifest") or {}, record=r, params=hit.params)
                if d["check"]["ok"] or _again:
                    return d
                why = "the image check now refuses it: " + "; ".join(d["check"]["problems"][:4])
            except RuntimeError as e:
                if _again: raise
                why = f"it no longer builds (the runtime or a fragment changed): {str(e)[:600]}"
            # the remembered op went stale: rewrite it for the record's own sentence, then serve this one again
            log(f"  (remembered op for {r['sentences'][0]!r} is stale — rewriting)")
            make_op(r["sentences"][0], log=log, rounds=rounds, feedback="the remembered op for this sentence " + why)
            d = make_op(sentence, log=log, rounds=rounds, _again=True)
            d["repronounced"] = {"from": "이전 발음", "to": "같은 몸", "why": [why[:160]], "how": "rebuild"}
            return d
        import intent
        recs = REC.all_records()
        m = intent.match_rule(sentence, recs)                  # same meaning, other words — no model
        if m:
            return _serve_match(sentence, m)
        key = hashlib.sha1((sentence + json.dumps(cat, sort_keys=True)).encode()).hexdigest()[:12]
        cache = os.path.join(BUILD, "ops", key + ".c")
        if os.path.exists(cache) and os.environ.get("POKE_LEGACY_OPS", "1") != "0":   # older cache: no manifest, no parameters
            src = open(cache).read()
            return _op_dict(src, key, cached=True, gen_s=0, rounds=0, manifest=_legacy_manifest(src), record=None, params={})
        if os.environ.get("POKE_INTENT_MODEL", "1") != "0" and intent.candidates(sentence, recs):
            t_m = time.time()
            try:                                                # one short judgement before a whole new op
                m = intent.match(sentence, recs, ask=lambda sys_, p: claude_cli.ask(sys_, p, timeout=60))
            except Exception as e:
                if type(e).__name__ in ("ModelBanned", "Skip"): raise
                m = None
            if m:
                return _serve_match(sentence, m, judge_s=round(time.time() - t_m, 1))
    t0, why0 = time.time(), (feedback or "")[:300]
    colophon = lambda rnd: {"model": os.environ.get("POKE_OP_MODEL", "Claude Code CLI (claude -p, default model)"),
                            "gen_s": round(time.time() - t0, 1), "rounds": rnd, "catalog": cat_key, **({"why": why0} if why0 else {})}
    tag = "gen-" + hashlib.sha1(sentence.encode()).hexdigest()[:10]
    for rnd in range(1, rounds + 1):
        if os.environ.get("POKE_ROUTE") == "1" and not feedback:   # the map picks the parts (knowledge.route)
            import knowledge
            cat, _route = knowledge.route(sentence, cat)
            print(f"[지도] {_route.get('rows', '?')}개 부품 중 {', '.join(_route.get('picked') or []) or '(전체 카탈로그)'} 선택"
                  + (f" — {_route['fallback']}" if _route.get("fallback") else f", 읽은 노드 {len(_route.get('read', []))}개"))
        raw = claude_cli.strip_fences(claude_cli.ask(OP_SYSTEM, op_prompt(sentence, cat, feedback)))
        reply = hub.reply_kind(raw)
        if reply:
            kind, body = reply
            REC.create_reply(sentence, kind, body, colophon(rnd))
            raise KO.Refusal(kind, body, "")
        src = raw
        probs = hub.op_problems(src, sentence)
        if probs:
            feedback = "the MANIFEST / PARAMS blocks are wrong: " + "; ".join(probs[:6]); log(f"  (round {rnd}: manifest/params, asking again)"); continue
        built = hub.with_hub_consts(src)
        try:
            img, obj, path = build(built, tag)
        except RuntimeError as e:
            feedback = f"it did not compile: {str(e)[:800]}"; log(f"  (round {rnd}: compile error, asking again)"); continue
        c = check(obj)
        if not c["ok"]:
            feedback = "the image check refused it: " + "; ".join(c["problems"][:6]); log(f"  (round {rnd}: image check refused, asking again)"); continue
        import manifest as MF
        m = MF.parse(src)
        r = REC.create_op(sentence, src, m, colophon(rnd))
        params = {p["name"]: REC.define_value(src, p["name"]) for p in r["params"]}
        return {"src": built, "img": img, "obj": obj, "path": path, "key": tag, "cached": False, "gen_s": round(time.time() - t0, 1),
                "rounds": rnd, "check": c, "manifest": m, "record": r, "params": params}
    raise Refused(f"no acceptable op after {rounds} rounds: {feedback}")

def default_bench():
    """What each sensor sees before power-on: bench.default_state() (base bench: {"t1 temperature": 23750})."""
    return _bench.default_state(BENCH)                        # the parts this body has now (BENCH may differ from POKE_BENCH)

# ── the person's side: CLI ──────────────────────────────────────────────
def _asker(answers):
    """Answers come from --answer (in order), then the terminal, then piped stdin lines; None = nobody to ask.
    Piped stdin is read once, up front: the Claude Code subprocess the hub may start would otherwise inherit
    the pipe and swallow the person's answers."""
    answers = list(answers or [])
    piped = []
    if sys.stdin is not None and not sys.stdin.isatty():
        try: piped = [l.strip() for l in sys.stdin.read().splitlines()]
        except (OSError, ValueError): piped = []
    def ask(kind, question):
        prompt = (f"   {question}\n   실행할까요? [y 이번만 / a 항상 / N] " if kind == "confirm" else f"   {question} ")
        if answers:
            a = answers.pop(0); print(prompt + a); return a
        if sys.stdin is not None and sys.stdin.isatty():
            try: return input(prompt).strip() or None
            except EOFError: return None
        while piped:
            line = piped.pop(0)
            if line:
                print(prompt + line); return line
        return None
    return ask

def run(sentence, show=False, bench=None, script=(), retry_fault=True, yes=False, no=False, answers=None, target=None, debug=False, preflight=False):
    """One sentence through the hub (hub.pipeline), printed in Korean. Returns {"sentence", "op", "boot", "stop"}.
    A refused image never boots (the pipeline stops at the check)."""
    import hub, messages_ko as KO
    target = target or ("pi" if PI else "netboot-twin" if NETBOOT_TWIN else "twin")
    out = {"sentence": sentence, "op": None, "boot": None, "stop": None}
    print(f"\n🗣  \"{sentence}\"")
    for ev in hub.pipeline(sentence, bench=bench, script=script, yes=yes, no=no, ask=_asker(answers), target=target, retry_fault=retry_fault,
                           preflight_first=preflight):
        st = ev["stage"]
        if st == "op" and ev.get("state") == "ready":
            mt = ev.get("matched")
            if ev.get("repronounced") and not mt:
                ev = {**ev, "cached": True}
                how_rp = "방금 이 몸에 맞게 다시 쓴 op"
            how = ("기억에서 (모델 호출 0)" if ev["cached"] else f"Claude Code가 {ev['gen_s']}초에 작성 ({ev['rounds']}회째)") if not mt else \
                  f"\"{mt['base']}\"와 같은 뜻 — " + ("규칙으로 판단, 모델 호출 0" if mt["how"] == "rule" else f"Claude Code가 {ev['gen_s']}초에 판정")
            ps = ", ".join(f"{k[2:]}={v}" for k, v in (ev.get("params") or {}).items())
            rp = ev.get("repronounced")
            if rp:
                print(f"[허브] 몸이 바뀜: {rp['from']} → {rp['to']} ({'; '.join(rp['why'])})")
                print(f"[허브] " + ("이 몸에서 전에 한 발음을 다시 씀 (모델 호출 0)" if rp["how"] == "lineage" else f"문장을 이 몸에 맞게 다시 발음 — Claude Code {rp.get('gen_s')}초"))
            if ev.get("repronounced") and not mt: how = how_rp
            print(f"[허브] op {how}{' · ' + ps if ps else ''} · 이미지 {ev['image_bytes']} B (op {ev['op_bytes']} B)")
            if show and ev.get("src"): print(ev["src"])
        elif st == "op" and ev.get("state") == "retry":
            print(f"[허브] 노드가 op를 멈췄습니다 — 이유를 붙여 새 op를 받습니다")
        elif st == "confirm" and ev.get("state") in ("ok", "not_needed"):
            print(f"[허브] 할 일: {ev['summary']}" + (f" · 확인: {ev['how']}" if ev.get("how") else ""))
        elif st == "check":
            print(f"[허브] 이미지 검사 {'통과' if ev['ok'] else '거부'}" + ("" if ev["ok"] else ": " + "; ".join(ev["problems"][:3])))
        elif st == "boot" and ev.get("state") == "on":
            print({"twin": "[노드] 전원 켬 → 부팅 (트윈)", "pi": "[노드] 이미지를 넷부팅 서버에 넘김 — 노드 부트로더가 가져감",
                   "netboot-twin": "[노드] 이미지를 넷부팅 서버에 넘김 — (가상) 부트로더가 TFTP로 가져감"}[target])
        elif st == "boot":
            for l in ev.get("say") or []:
                if KO.korean(l) or debug: print(f"   노드: {l}")     # older ops narrate in English: shown with --debug
        elif st == "result":
            out["result_ok"] = bool(ev.get("ok"))
            for l in ev.get("lines") or []: print(f"[결과] {l}")
            if ev.get("problem"): print(f"[문제] {ev['problem']}")
            for l in ev.get("effects") or []: print(f"[버스] {l}")
            if debug: print(f"[raw] {json.dumps(ev.get('result'), ensure_ascii=False)} · 실험대 {json.dumps(ev.get('truth'))}")
        elif st == "empty":
            print(f"[노드] {ev.get('text') or ('꺼짐' if ev['powered_off'] else '강제로 끔')} · 전체 {ev['total_s']} s")
        elif st == "schedule":
            if ev["state"] == "plan": print(f"[허브] {ev['text']}")
            elif ev["state"] == "boot": print(f"   #{ev['i'] + 1} (+{ev['at_s']} s) " + " · ".join(ev.get("lines") or []))
        elif st == "trace_check":
            print(f"[흔적] {ev['text']} (이전 흔적 {ev.get('ref', 0)}개와 대조)")
            for x in ev.get("reasons") or []: print(f"       · {x}")
        elif st == "watch":
            if ev["state"] == "plan": print(f"[감시] {ev['text']}")
            elif ev["state"] == "armed": print(f"[감시] {ev['text']}")
            elif ev["state"] == "first":
                l = ev.get("last") or {}
                print(f"[감시] 첫 확인 {l.get('at', '')}: " + " · ".join(l.get("lines") or []) + (f" ({l['why']})" if l.get("why") else ""))
                for e in ev.get("events") or []: print(f"[감시] {e['text']}")
                out["result_ok"] = l.get("verdict") in ("ok",)
        elif st == "body":
            print(f"[몸] {ev['text']}" + (" — 문장을 한 번 더 처리합니다" if ev.get("rerun") else ""))
        elif st == "preflight":
            _print_preflight(ev["rows"])
        elif st == "stop":
            out["stop"] = {k: ev.get(k) for k in ("kind", "text", "hint")}
            print(f"[허브] {ev['text']}")
            if ev.get("hint"): print(f"       {ev['hint']}")
        elif st == "error_detail" and debug:
            print(f"[debug] {ev['text']}")
        elif st == "done":
            out["op"], out["boot"] = ev.get("op"), ev.get("boot")
    return out

PI = "--pi" in sys.argv
NETBOOT_TWIN = "--netboot-twin" in sys.argv

def _print_preflight(rows):
    mark = {"OK": "통과", "WARN": "주의", "FAIL": "실패"}
    for r in rows:
        print(f"[점검] {mark.get(r.get('level'), r.get('level')):<2} {r.get('check'):<9} {r.get('msg', '')[:150]}")

USAGE = """사용법: say.py [옵션] "문장"
  --show            op 소스도 보여 줌
  --yes / --no      확인 질문에 자동으로 예 / 아니오 (예약 실행·시험용)
  --answer "…"      질문(확인·되물음)에 미리 답함, 여러 번 쓰면 차례로
  --debug           내부 오류와 원시 결과까지 보여 줌
  --pi / --netboot-twin   실기기 / 가상 넷부팅 경로
  --pi --preflight  실기기로 보내기 전에 netboot/preflight.py 점검(FAIL이면 부팅 안 함)
  say.py --preflight      문장 없이 실기기 준비 점검만
  say.py selftest   조각마다 실험대 진실과 대조
  say.py memory [문장]           허브가 기억하는 문장 목록 / 한 문장의 카드
  say.py forget "문장"           기록 지우기(확인 받음)
  say.py unapprove "문장"        '항상' 승인 취소
  say.py card export "문장" [파일] · card import 파일 [--replace]   카드 옮기기
  --body 파일                    트윈의 몸을 바꿔서 (예: teams/product/bodies/tmp105-0x4a.json)
  say.py watch [run | stop <id|all> | events]   지켜보는 문장(상시 의도) 목록 / 감시 돌리기 / 멈춤 / 사건
  say.py learn 0x18              "배워줘": 처음 보는 부품을 배우기(확인 받음, 진행 표시)"""

def _learn_cmd(addr, opts):
    """say.py learn 0x18 — "배워줘": the hub learns a part it has just met (실기기 learn.py), with progress."""
    import learn
    ans = "y" if opts.get("yes") else _asker(opts.get("answers"))("confirm", f"{addr}의 부품을 배울까요? (몇 분, Claude Code 여러 번)")
    if not ans or not re.match(r"^\s*(y|yes|네|예|응|a)", ans, re.I):
        print("[허브] 배우지 않았습니다."); return 3
    j = learn.start(addr)
    print(f"[배움] 시작 ({j['id']}) — 끝날 때까지 기다립니다 (Ctrl+C로 기다리기만 그만, 작업은 계속)")
    def on_ev(e):
        print(f"[배움] {learn.STAGE_KO.get(e.get('stage'), e.get('stage'))} {e.get('state', '')} " + " ".join(str(e[k]) for k in ("part", "confidence", "round", "why") if e.get(k) is not None))
    try:
        st = learn.wait(j["id"], on_event=on_ev)
    except KeyboardInterrupt:
        print("\n[배움] 기다리기를 그만둡니다 — 작업은 계속, 'learn.py status " + j["id"] + "'로 확인"); return 0
    res = (st or {}).get("result") or {}
    if st.get("state") == "done" and res.get("learned"):
        print(f"[배움] 배웠습니다: {res.get('registered')}"); return 0
    print(f"[배움] 배우지 못했습니다{': ' + str(res.get('why')) if res.get('why') else ''} — 허브는 이 주소를 '모름'으로 기억합니다"); return 1

def _watch_cmd(rest, opts):
    """say.py watch [list] · watch run · watch stop <id|all> · watch events"""
    import watch
    cmd = rest[0] if rest else "list"
    if cmd == "run":
        print(f"[감시] 지켜보는 중인 문장을 확인합니다 (Ctrl+C로 끝). 상한: {WATCH}")
        try: watch.run_forever()
        except KeyboardInterrupt: print("\n[감시] 멈춤 (무장은 그대로 — 다시 run하면 이어서)")
        return 0
    if cmd == "stop" and len(rest) > 1:
        ws = watch.stop_all() if rest[1] == "all" else [watch.stop(rest[1])]
        print(f"[감시] 멈춤: {', '.join(w['id'] for w in ws if w) or '없음'}"); return 0
    if cmd == "events":
        for e in watch.events(30): print(f"  {e['at'][11:]} {e['kind']:<14} {e['text']}")
        return 0
    ws = [watch.describe(w) for w in watch.all_watches()]
    if not ws:
        print("[감시] 지켜보는 문장이 없습니다. 예: say.py \"온도가 30도 넘으면 알려줘\""); return 0
    ko = {"armed": "지켜보는 중", "resting": "오늘 상한 — 쉼", "stopped": "멈춤", "expired": "만료", "done": "알리고 끝"}
    for w in ws:
        last = w["last"] or {}
        print(f"  {w['id']}  {ko.get(w['state'], w['state']):<10} {w['sentence']}  · {w['every_s']:g}초마다 · 오늘 {w['count_today']}/{w['day_cap']}"
              + (f" · 마지막 {last.get('at')} {' · '.join(last.get('lines') or [])}" if last else "")
              + (f" · {w['stop_reason']}" if w.get("stop_reason") else ""))
    return 0

def _memory_cmd(args, opts):
    """say.py memory [문장] · forget "문장" · unapprove "문장" · card export "문장" [파일] · card import 파일 [--replace]"""
    import record as REC
    cmd, rest = args[0], args[1:]
    if cmd == "memory":
        return REC.main(" ".join(rest) if rest else None) or 0
    if cmd == "card" and rest[:1] == ["import"] and len(rest) >= 2:
        try:
            r, msg = REC.import_card(json.load(open(rest[1])), replace=opts.get("replace", False))
        except (OSError, ValueError) as e:
            print(f"[허브] 카드를 가져오지 못했습니다: {e}"); return 4
        print(f"[허브] {msg}: 「{r['sentences'][0]}」"); print("\n".join(REC.card_lines(r))); return 0
    if cmd == "card" and rest[:1] == ["export"] and len(rest) >= 2:
        sentence, path = rest[1], (rest[2] if len(rest) > 2 else None)
    else:
        sentence, path = " ".join(rest), None
    r = REC.find(sentence) if sentence else None
    if not r:
        print(f"[허브] 「{sentence}」에 대한 기록이 없습니다. (say.py memory로 목록)"); return 3
    if cmd == "card":
        card = REC.export_card(r)
        path = path or os.path.join(BUILD, "cards", r["id"] + ".json")
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        json.dump(card, open(path, "w"), ensure_ascii=False, indent=1)
        print(f"[허브] 카드를 내보냈습니다: {path}"); return 0
    if cmd == "unapprove":
        REC.unapprove(r); print(f"[허브] 「{r['sentences'][0]}」의 '항상' 승인을 취소했습니다. 다음부터 다시 묻습니다."); return 0
    # forget: the whole record — ask first
    print("\n".join(REC.card_lines(r)))
    ans = "y" if opts.get("yes") else _asker(opts.get("answers"))("forget", "이 기록을 지울까요? 결과 흔적과 계보도 함께 지워집니다. [y/N]")
    if not ans or not re.match(r"^\s*(y|yes|네|예|응)", ans, re.I):
        print("[허브] 지우지 않았습니다."); return 3
    REC.forget(r["id"]); print(f"[허브] 「{r['sentences'][0]}」 기록을 지웠습니다. 다음에 이 말을 들으면 새로 씁니다."); return 0

def main(argv):
    opts, args, it = {"answers": []}, [], iter(argv)
    for a in it:
        if a == "--answer": opts["answers"].append(next(it, ""))
        elif a.startswith("--answer="): opts["answers"].append(a.split("=", 1)[1])
        elif a == "--body": opts["body"] = next(it, "")
        elif a.startswith("--body="): opts["body"] = a.split("=", 1)[1]
        elif a.startswith("--"): opts[a[2:]] = True
        else: args.append(a)
    if opts.get("body"):                                      # a twin body described in a file (body.py)
        import body
        try:
            b = body.load_body_file(opts["body"]); print(f"[몸] {body.label(b)}")
        except (OSError, ValueError) as e:
            print(f"[허브] 몸 설명 파일을 읽지 못했습니다: {e}"); return 4
    if not args and opts.get("preflight"):
        import hub
        rows, failed = hub.preflight()
        _print_preflight(rows)
        print("[허브] 실기기 준비 " + ("완료" if not failed else "안 됨: " + ", ".join(dict.fromkeys(hub.PREFLIGHT_KO.get(f, f) for f in failed))))
        return 0 if not failed else 3
    if not args:
        print(USAGE); return 2
    if args[0] == "selftest" and len(args) == 1:
        import selftest; return 0 if selftest.main() else 1
    if args[0] in ("memory", "forget", "unapprove", "card"):
        return _memory_cmd(args, opts)
    if args[0] == "watch":
        return _watch_cmd(args[1:], opts)
    if args[0] == "learn" and len(args) == 2:
        return _learn_cmd(args[1], opts)
    import messages_ko as KO
    try:
        r = run(" ".join(args), show=opts.get("show", False), yes=opts.get("yes", False), no=opts.get("no", False), preflight=opts.get("preflight", False),
                answers=opts["answers"], debug=opts.get("debug", False))
    except KeyboardInterrupt:
        print("\n[허브] 중단했습니다."); return 130
    except Exception as e:                                    # last line of defence: never a traceback for a person
        text, hint = KO.explain(e)
        print(f"[허브] {text}" + (f"\n       {hint}" if hint else ""))
        if opts.get("debug"): raise
        return 4
    if r["stop"]:
        return 4 if r["stop"]["kind"] in ("error", "refused") else 3
    return 0 if r.get("result_ok") else 1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
