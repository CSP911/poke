#!/usr/bin/env python3
"""world — the virtual home beside the hub: three rooms whose sensors follow what the actuators do.

QEMU's parts have knobs but no physics between them: an air conditioner's pin going high does not cool the
TMP105 next to it. This module is that physics, kept outside the node (the node never sees it):

    before each boot   the world → the twin's knobs: sensor values, and every role pin's level
                       (inputs = the world's contact/motion/leak; outputs = what the actuator is doing now,
                       so a read-back tells the truth and a boot that touches nothing changes nothing)
    after each boot    the twin's pins → the world: a switch, curtain or air conditioner the op drove
    advance(min)       time passes: sun on the west window in the evening, heat leaking toward the outdoors,
                       the air conditioner pulling the room toward its target, the washer's program …

The rules are deliberately few and visible (RULES below) — enough that a purpose like "거실이 왜 저녁마다
더운지 찾아줘" has a real answer to find (west sun through an open curtain, 16:00–19:30), and that "에어컨
24도로 켜줘" can be checked by the room actually cooling.

    ../sim/body/.venv/bin/python world.py show | reset [HH:MM] | advance <minutes> | set <thing> <value>
    POKE_WORLD=<file> picks the state file (tests use a temporary one; default build/world.json).
"""
import os, sys, json, math
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RULES = {
    "outdoor_c":        "26 + 6·cos(2π(h−15)/24) — 32 °C at 15:00, 20 °C at 03:00 (a summer day)",
    "leak_per_min":     "a room moves 1 %/min toward the outdoors (5 %/min with a window open)",
    "west_sun":         "living room: +0.08 °C/min × sun(16:00–19:30, peak 17:45); a closed curtain lets 25 % through",
    "east_sun":         "bedroom: +0.04 °C/min × sun(07:30–10:30); the window-side probe reads +3 °C × sun",
    "aircon":           "on: the room falls toward the target by up to 0.1 °C/min",
    "washer":           "plug on: a 45-minute program at ~400 W (48 V stand-in line), then 2 W standby",
    "boiler":           "pipe 55 °C while heating 06:00–08:00 and 18:00–22:00, else cooling 2 %/min toward the room",
}

def path():
    return os.environ.get("POKE_WORLD") or os.path.join(HERE, "build", "world.json")

def fresh(at="15:00"):
    h, m = map(int, at.split(":"))
    return {"t": h * 60 + m,
            "room": {"living": 27.0, "bedroom": 25.5, "utility": 23.0},
            "pipe": 30.0,
            "act": {"living/ceiling_light": 0, "living/mood_light": 0, "living/curtain": "open",
                    "living/aircon": {"on": 0, "target": 24},
                    "bedroom/ceiling_light": 0, "bedroom/desk_lamp": 0, "utility/washer_plug": 0},
            "input": {"living/motion": 1, "bedroom/window": 0, "utility/leak": 0},
            "washer_left": 0,
            "events": [],                 # [{"at": minute, "thing": "living/motion", "value": 0}] — the world's script
            "history": []}

def load():
    try: return json.load(open(path()))
    except (OSError, ValueError): return fresh()

def save(w):
    os.makedirs(os.path.dirname(path()), exist_ok=True)
    json.dump(w, open(path(), "w"), indent=1, ensure_ascii=False)

def clock(w):
    t = int(w["t"]); return f"day {t // 1440} {t % 1440 // 60:02d}:{t % 60:02d}"

# ── physics ─────────────────────────────────────────────────────────────
def outdoor(t):
    h = (t % 1440) / 60
    return 26 + 6 * math.cos(2 * math.pi * (h - 15) / 24)

def _bump(t, start, end):
    h = (t % 1440) / 60
    return math.sin(math.pi * (h - start) / (end - start)) if start <= h <= end else 0.0

def west_sun(t): return _bump(t, 16.0, 19.5)
def east_sun(t): return _bump(t, 7.5, 10.5)

def _step(w):
    t, R, A, I = w["t"], w["room"], w["act"], w["input"]
    out = outdoor(t)
    # living room: leak, west sun through the curtain, the air conditioner
    T = R["living"]
    T += 0.01 * (out - T) + 0.08 * west_sun(t) * (1.0 if A["living/curtain"] == "open" else 0.25)
    ac = A["living/aircon"]
    if ac["on"] and T > ac["target"]:
        T -= min(0.1, T - ac["target"])
    R["living"] = T
    # bedroom: leak (more with the window open), morning sun
    k = 0.05 if I["bedroom/window"] else 0.01
    R["bedroom"] += k * (out - R["bedroom"]) + 0.04 * east_sun(t)
    # utility: slow, the boiler pipe warms it a little
    R["utility"] += 0.005 * (out - R["utility"]) + 0.002 * (w["pipe"] - R["utility"])
    h = (t % 1440) / 60
    heating = 6 <= h < 8 or 18 <= h < 22
    w["pipe"] = 55.0 if heating else w["pipe"] + 0.02 * (R["utility"] - w["pipe"])
    # washer program
    if A["utility/washer_plug"] and w["washer_left"] > 0:
        w["washer_left"] -= 1
    w["t"] = t + 1
    for e in [e for e in w["events"] if e["at"] <= w["t"]]:
        _set(w, e["thing"], e["value"]); w["events"].remove(e)

def advance(w, minutes):
    for _ in range(int(minutes)):
        _step(w)
        if w["t"] % 15 == 0:
            w["history"].append(snapshot(w)); w["history"] = w["history"][-200:]
    return w

def snapshot(w):
    return {"clock": clock(w), "outdoor": round(outdoor(w["t"]), 2), **{k: round(v, 2) for k, v in w["room"].items()},
            "pipe": round(w["pipe"], 2), "curtain": w["act"]["living/curtain"], "aircon": dict(w["act"]["living/aircon"]),
            "washer_w": round(washer_mw(w) / 1000, 1)}

def _set(w, thing, value):
    if thing in w["input"]: w["input"][thing] = int(value)
    elif thing == "living/aircon": w["act"][thing] = dict(value)
    elif thing in w["act"]:
        w["act"][thing] = value
        if thing == "utility/washer_plug" and value and not w["washer_left"]: w["washer_left"] = 45
    else: raise KeyError(thing)

def washer_mw(w):
    if not w["act"]["utility/washer_plug"]: return 0
    return 400_000 if w["washer_left"] > 0 else 2_000

# ── world ↔ twin ────────────────────────────────────────────────────────
# sensor knobs by node and the attachment's id on that node's twin (the world's own wiring — the hub never sees it)
def _sensors(node_id, w):
    t, R = w["t"], w["room"]
    mC = lambda c: int(round(c * 1000))
    if node_id == "living":
        return {"t1 temperature": mC(R["living"])}
    if node_id == "bedroom":
        window = R["bedroom"] + 3 * east_sun(t) + 0.3 * (outdoor(t) - R["bedroom"])
        return {"x1 temperature0": mC(R["bedroom"]), "x1 temperature1": mC(window)}
    if node_id == "utility":
        p = washer_mw(w); v = 48_000
        return {"c1 temperature0": mC(R["utility"]), "c1 temperature1": mC(w["pipe"]), "c1 temperature2": mC(w["pipe"] - 8.5),
                "c5 vin": v, "c5 vout": v - 40, "c5 iout": p * 1000 // v, "c5 pin": p // 1000}   # the twin takes input power in whole W
    return {}

def _roles(node_id):
    import registry as R
    for a in R.attachments(node_id):
        for r in a.get("roles") or []:
            yield a, r

def knobs(node_id, w=None):
    """What the twin must look like at power-on for this node: sensors, and every role pin's level."""
    w = w or load()
    lv = lambda x: "high" if x else "low"
    st = _sensors(node_id, w)
    for a, r in _roles(node_id):
        thing, q = f"{node_id}/{r['key']}", a["twin_id"]
        if r["role"] in ("contact", "motion", "leak"):
            st[f"{q} pin{r['pin']}"] = lv(w["input"].get(thing, 0))
        elif r["role"] == "switch":
            st[f"{q} pin{r['pin']}"] = lv(w["act"].get(thing, 0))
        elif r["role"] == "cover":                      # the motor lines are idle at power-on; the position is the world's
            st[f"{q} pin{r['open']}"] = "low"; st[f"{q} pin{r['close']}"] = "low"
            if "closed" in r:                           # the closed-limit switch tells where the curtain is
                st[f"{q} pin{r['closed']}"] = lv(w["act"].get(thing) == "closed")
        elif r["role"] == "climate":
            ac = w["act"].get(thing, {"on": 0, "target": 0})
            st[f"{q} pin{r['power']}"] = lv(ac["on"])
            for i in range(int(r.get("bits", 8))):
                st[f"{q} pin{r['base'] + i}"] = lv(ac["on"] and (ac["target"] >> i) & 1)
    return st

def observe(node_id, boot, w=None):
    """The twin's pins after the node powered off → what the actuators now do. Returns the changes."""
    keep = w is None
    w = w or load()
    pins = (((boot or {}).get("truth") or {}).get("ext") or {}).get("pins") or {}
    hi = lambda q, p: (pins.get(q) or {}).get(str(p), (pins.get(q) or {}).get(p)) == "high"
    changed = {}
    for a, r in _roles(node_id):
        q, thing = a["twin_id"], f"{node_id}/{r['key']}"
        if q not in pins: continue
        if r["role"] == "switch":
            v = int(hi(q, r["pin"]))
        elif r["role"] == "cover":
            o, c = hi(q, r["open"]), hi(q, r["close"])
            v = "open" if o and not c else "closed" if c and not o else w["act"][thing]
        elif r["role"] == "climate":
            on = int(hi(q, r["power"]))
            v = {"on": on, "target": sum(1 << i for i in range(int(r.get("bits", 8))) if hi(q, r["base"] + i)) if on
                 else w["act"][thing]["target"]}
        else:
            continue
        if v != w["act"].get(thing):
            changed[thing] = {"was": w["act"].get(thing), "now": v}; _set(w, thing, v)
    if changed:
        w.setdefault("acted", []).append({"clock": clock(w), "node": node_id, **changed}); w["acted"] = w["acted"][-100:]
    if keep: save(w)
    return changed

if __name__ == "__main__":
    a = sys.argv[1:] or ["show"]
    if a[0] == "reset":
        w = fresh(a[1] if len(a) > 1 else "15:00"); save(w)
    elif a[0] == "advance":
        w = advance(load(), int(a[1])); save(w)
    elif a[0] == "set":
        w = load(); v = a[2]
        _set(w, a[1], json.loads(v) if v[:1] in "{[0123456789" else v); save(w)
    else:
        w = load()
    print(f"{clock(w)} · {path()}")
    print(json.dumps(snapshot(w), ensure_ascii=False))
    print("act", json.dumps(w["act"], ensure_ascii=False)); print("input", json.dumps(w["input"], ensure_ascii=False))
    if a[0] == "rules":
        for k, v in RULES.items(): print(f"  {k:14} {v}")
