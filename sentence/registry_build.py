#!/usr/bin/env python3
"""Build the hub's body registry (sentence/registry/parts) from what exists today: the fragment catalog,
the incubation records and the safety-reviewed function policy (manifest.FN + incubated/fn.json).

Layer L2 (part types) gets, per part, the L3 capabilities it implements: which channels, the C body that
implements the capability's call on this chip, and the fragment functions that body uses. Permissions are
not written here by hand — each binding's policy is derived from the functions it uses (the strictest wins).

    ../sim/body/.venv/bin/python registry_build.py          → registry/parts/*.json

BINDINGS is where a person (today) or an incubation (next) says how a part implements a capability.
In a body, `ADDR` and `CH` are replaced by the attachment's address and the channel's value; the other
names are the capability call's own parameters (registry/caps/<cap>.json).
"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import say, manifest as MF

REG = os.path.join(HERE, "registry")

def chans(n): return {str(i): i for i in range(n)}

BINDINGS = {
    "tmp105": [{"cap": "temperature.read", "channels": {"": 0}, "body": "return tmp105_read_mC(ADDR, milli_c);",
                "spec": {"unit": "m°C", "resolution": 62.5, "basis": "fragment (12-bit)"}}],
    "tmp421": [{"cap": "temperature.read", "channels": {"local": 0, "remote": 1}, "body": "return tmp421_read_mc(ADDR, CH, milli_c);",
                "spec": {"unit": "m°C", "resolution": 62.5, "range": [0, 127000], "basis": "fragment"}}],
    "emc1403": [{"cap": "temperature.read", "channels": {"internal": 0, "remote1": 1, "remote2": 2},
                 "body": "return emc1403_read(ADDR, CH, milli_c);",
                 "spec": {"unit": "m°C", "resolution": 125, "range": [0, 127875], "basis": "fragment"}}],
    "dps310": [{"cap": "temperature.read", "channels": {"": 0}, "body": "long p_; return dps310_read(ADDR, milli_c, &p_);",
                "spec": {"unit": "m°C", "range": [-40000, 85000], "basis": "fragment"}},
               {"cap": "pressure.read", "channels": {"": 0}, "body": "long t_; return dps310_read(ADDR, &t_, pa);",
                "spec": {"unit": "Pa", "range": [30000, 120000], "basis": "fragment"}}],
    "mag": [{"cap": "magnetic.read", "channels": {"": 0}, "body": "return mag_read(ADDR, x, y, z);",
             "spec": {"unit": "counts", "basis": "fragment"}}],
    "rtc": [{"cap": "time.read", "channels": {"": 0}, "body": "return rtc_read(ADDR, year, month, day, hour, min, sec);",
             "spec": {"unit": "24-hour local", "basis": "fragment"}},
            {"cap": "time.set", "channels": {"": 0}, "body": "return rtc_set(ADDR, year, month, day, hour, min, sec);"}],
    "led": [{"cap": "switch.set", "channels": chans(16), "body": "return led_set(ADDR, CH, on ? 0 : 1);", "what": "LED"},
            {"cap": "switch.read", "channels": chans(16),
             "body": "unsigned p_; int r_ = led_inputs(ADDR, &p_); if (!r_) *on = !((p_ >> CH) & 1); return r_;", "what": "LED"}],
    "pca9554": [{"cap": "switch.set", "channels": chans(8), "body": "return pca9554_set_pin(ADDR, CH, on ? 1 : 0);", "what": "output pin"},
                {"cap": "switch.read", "channels": chans(8),
                 "body": "unsigned l_; int r_ = pca9554_read(ADDR, &l_); if (!r_) *on = (l_ >> CH) & 1; return r_;", "what": "pin level"}],
    "pca9555": [{"cap": "switch.set", "channels": chans(16), "body": "return pca9555_set_pin(ADDR, CH, on ? 1 : 0);", "what": "output pin"},
                {"cap": "switch.read", "channels": chans(16),
                 "body": "unsigned l_; int r_ = pca9555_read(ADDR, &l_); if (!r_) *on = (l_ >> CH) & 1; return r_;", "what": "pin level"}],
    "ssd0303": [{"cap": "display.text", "channels": {"": 0},
                 "body": "int r_ = ssd0303_on(ADDR); return r_ ? r_ : ssd0303_text(ADDR, line, 0, s);"}],
    "adm1272": [{"cap": "power.measure", "channels": {"": 0},
                 "body": "long vo_; int r_ = adm1272_read_vi(ADDR, PARAM_RS_UOHM, vin_mv, &vo_, iout_ma); return r_ ? r_ : adm1272_read_pin(ADDR, PARAM_RS_UOHM, pin_mw);",
                 "params": {"PARAM_RS_UOHM": "sense resistor in µΩ (attachment parameter)"},
                 "spec": {"unit": "mV, mA, mW", "basis": "fragment"}}],
}

ORDER = {"allow": 0, "confirm": 1, "deny": 2}

def fns_of(body):
    return sorted(fn for fn in MF.FN if f"{fn}(" in body.replace(" (", "("))

def build():
    cat = say.catalog() if False else json.load(open(os.path.join(say.FRAGS, "catalog.json")))
    os.makedirs(os.path.join(REG, "parts"), exist_ok=True)
    out = []
    for name, e in sorted(cat.items()):
        rec_p = os.path.join(HERE, "incubated", f"{name}.json")
        rec = json.load(open(rec_p)) if os.path.exists(rec_p) else {}
        impl = []
        for b in BINDINGS.get(name, []):
            fns = fns_of(b["body"])
            table = MF.policy()                                  # the owner's table (manifest.POLICY + policy.json); absent → deny
            pol = max((table.get(f, "deny") for f in fns), key=lambda p: ORDER.get(p, 2), default="deny")
            access = "write" if any(MF.FN[f].get("access") == "write" for f in fns) else "read"
            impl.append({**b, "fns": fns, "access": access, "policy": pol})
        part = {"id": name, "identity": rec.get("identity") or e.get("from_driver") or name,
                "fragment": f"{name}.h", "default_addr": e.get("default_addr"),
                "identify": e.get("identify") or rec.get("identify"),
                "verification": ({"kind": rec.get("verification_kind") or ("twin-knobs" if rec.get("twin_knobs") else "?"),
                                  "on": rec.get("bench"), "rounds": rec.get("rounds"), "when": rec.get("learned")}
                                 if rec else {"kind": "library driver (earlier incubation)"}),
                "implements": impl,
                "functions": {f: {**{k: MF.FN[f].get(k) for k in ("access", "write_regs", "max_write")}, "policy": MF.policy().get(f, "deny")}
                              for f in sorted({f for b in impl for f in b["fns"]})},
                "unbound": [] if name in BINDINGS else ["no capability binding yet — invisible to plans"]}
        json.dump(part, open(os.path.join(REG, "parts", f"{name}.json"), "w"), indent=1, ensure_ascii=False)
        out.append((name, [f"{b['cap']}[{len(b['channels'])}]:{b['policy']}" for b in impl]))
    return out

if __name__ == "__main__":
    for n, caps in build():
        print(f"{n:9} {', '.join(caps) or '(none)'}")
