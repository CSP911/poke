"""bench — what the twin's body is: the I2C devices on bus 1 and how to read their real state (실기기팀, G1).

    BASE   the default bench (unchanged; every regress case assumes it)
    EXT    BASE + parts the hub has never seen: a TMP421 remote/local temperature sensor, a PCA9554
           8-bit GPIO expander, an SSD0303 OLED display (96×16), a DPS310 pressure sensor (B3; the twin
           has no control for what it measures — fixed readings) — for discovering and incubating
           new devices (sentence/incubation.py) and the sentences that use them afterwards.

Which bench a process uses: POKE_BENCH=ext (environment) or bench.use("ext"). say.BENCH follows it.

The twin's truth for the new parts (read over QMP after the node powers off):
    tmp421   temperature0..3 (m°C, settable before power-on: the room the sensor sees)
    pca9554  pin0..7 ("high"/"low"; set = drive an input, get = the pin level, outputs included)
    ssd0303  the screen: QMP screendump of its console (384×64 = 96×16 pixels magnified 4×)
"""
import os, re

BASE = [("tmp105", "t1", 0x48), ("ds1338", "r1", 0x68), ("lsm303dlhc_mag", "m1", 0x1e), ("pca9552", "l1", 0x60)]
NEW = [("tmp421", "x1", 0x4c), ("pca9554", "g1", 0x20), ("ssd0303", "o1", 0x3c), ("dps310", "p1", 0x77)]
EXT = BASE + NEW
# a part with nothing on the twin to check it by (no control, contents arbitrary): the reliability runs'
# negative control (teams/hw/t_reliability.py)
EEPROM = BASE + [("at24c-eeprom,rom-size=256", "e2", 0x50)]
EMC = BASE + [("emc1413", "e1", 0x18)]                  # a new sensor never learned: reliability runs
# the RouteMind experiment (2026-10-13): three parts never learned by the owner's hub — a 4-channel
# temperature sensor, a 16-pin GPIO expander, a hot-swap power monitor (PMBus)
LAB = BASE + [("emc1413", "c1", 0x18), ("pca9535", "c3", 0x24), ("adm1272", "c5", 0x10)]
# the same kinds again at other addresses: what a second building's bench looks like
LAB2 = BASE + [("emc1413", "c1", 0x19), ("adm1272", "c5", 0x11)]
BENCHES = {"base": BASE, "ext": EXT, "eeprom": EEPROM, "emc": EMC, "lab": LAB, "lab2": LAB2}

def name():
    n = os.environ.get("POKE_BENCH", "base").strip().lower() or "base"
    return n if n in BENCHES else "base"

def active():
    return list(BENCHES[name()])

def use(n):
    """Switch this process (and say.BENCH) to bench n."""
    os.environ["POKE_BENCH"] = n
    try:
        import say
        say.BENCH = active()
    except ImportError:
        pass
    return active()

def default_state(bench=None):
    """Bench state set before power-on: the room each sensor sees, the levels on input pins."""
    s = {}
    devs = {q: m for m, q, _ in (bench or active())}
    if "t1" in devs:
        s["t1 temperature"] = 23750
    if "x1" in devs:
        s.update({"x1 temperature0": 24500, "x1 temperature1": 36250})       # local (board) and remote (probe)
    if "g1" in devs:
        s.update({"g1 pin0": "high", "g1 pin1": "low", "g1 pin2": "high", "g1 pin3": "low"})   # switches on 0–3
    return s

# ── screen ──────────────────────────────────────────────────────────────
W, H, MAG = 96, 16, 4

def read_ppm(path):
    d = open(path, "rb").read()
    m = re.match(rb"P6\s+(\d+)\s+(\d+)\s+(\d+)\s", d)
    w, h = int(m.group(1)), int(m.group(2))
    px = d[m.end():]
    return w, h, px

def screen_bits(path):
    """The display as 16 rows × 96 columns of 0/1 (sampled at the centre of each 4×4 dot)."""
    w, h, px = read_ppm(path)
    mag = w // W
    rows = []
    for y in range(h // mag):
        r = []
        for x in range(w // mag):
            o = ((y * mag + mag // 2) * w + (x * mag + mag // 2)) * 3
            r.append(1 if px[o] + px[o + 1] + px[o + 2] > 3 * 96 else 0)
        rows.append(r)
    return rows

def ascii_art(rows):
    return "\n".join("".join("#" if b else "." for b in r) for r in rows)

def truth(mon, bench=None, shot_dir=None):
    """Real state of the extended parts after power-off (the base parts are read by say.boot)."""
    out = {}
    for model, qid, addr in (bench or active()):
        if model == "tmp421":
            out["tmp421_mC"] = [mon.get(qid, f"temperature{i}") for i in range(2)]
        elif model == "pca9554":
            pins = {i: mon.get(qid, f"pin{i}") for i in range(8)}
            out.setdefault("pca9554_pins", pins)                          # the first expander (older readers)
            out.setdefault("pins", {})[qid] = pins                        # every expander, by its id
        elif model == "pca9535":
            out["pca9535_pins"] = {i: mon.get(qid, f"led{i}") for i in range(16)}   # this QEMU model names its pins led0–15
        elif model == "ssd0303":
            d = shot_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "build", "screens")
            os.makedirs(d, exist_ok=True)
            f = os.path.join(d, f"{os.getpid()}-{id(mon)}.ppm")
            mon.call("screendump", filename=f, device=qid)
            import time
            for _ in range(50):
                if os.path.exists(f) and os.path.getsize(f) > 100: break
                time.sleep(0.02)
            rows = screen_bits(f); os.remove(f)
            out["screen"] = rows
            out["screen_lit"] = sum(map(sum, rows))
    return out

# ── boot on a chosen bench ──────────────────────────────────────────────
import threading as _th
_SWAP = _th.Lock()

def boot(img_path, state=None, bench=None, budget_s=30):
    """say.boot on `bench` (default: say.BENCH, the process's current body) with its default state. say.boot reads the extended
    parts' truth itself (truth["ext"]) and adds wipe_check and effects; this wrapper only picks the bench."""
    import say
    bench = list(bench or say.BENCH)                 # the body this process is talking to (tests may move parts)
    st = default_state(bench) if state is None else state
    if bench == list(say.BENCH):
        b = say.boot(img_path, st, budget_s=budget_s)
    else:
        with _SWAP:                                   # say.boot reads the module's BENCH
            keep = say.BENCH; say.BENCH = bench
            try: b = say.boot(img_path, st, budget_s=budget_s)
            finally: say.BENCH = keep
    b.setdefault("truth", {}).setdefault("ext", {})
    return b
