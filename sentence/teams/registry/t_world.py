#!/usr/bin/env python3
"""The virtual home (world.py) and pin roles, without a model: a role op drives the twin's pins, the world
learns what the actuators now do, the next boot reads that back, time passing changes the rooms the way the
rules say, and a boot that only reads changes nothing. Runs on a temporary world file (POKE_WORLD)."""
import os, sys, tempfile, json
HERE = os.path.dirname(os.path.abspath(__file__)); SENT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, SENT)
os.environ["POKE_WORLD"] = os.path.join(tempfile.mkdtemp(prefix="poke-world-"), "world.json")
import registry as R, world as W

ok, n = 0, 0
def check(name, cond, detail=""):
    global ok, n
    n += 1; ok += bool(cond)
    print(f"{'PASS' if cond else 'FAIL'} {name} {detail}")

W.save(W.fresh("17:00"))
inst = {(i["cap"], i.get("key") or i.get("channel")): i for i in R.instances("living")}
K = lambda cap, key: inst[(cap, key)]["const"]
READ = [("temperature.read", ""), ("climate.read", "aircon"), ("cover.read", "curtain"), ("switch.read", "ceiling_light")]
read_op = f'''void op_main(void){{ long v; int on, t, c, l;
 if (cap_temperature_read({K(*READ[0])},&v) || cap_climate_read({K(*READ[1])},&on,&t) || cap_cover_read({K(*READ[2])},&c) || cap_switch_read({K(*READ[3])},&l)) {{ fail("r"); return; }}
 report_int("mC",v); report_int("ac_on",on); report_int("ac_t",t); report_int("closed",c); report_int("light",l); }}'''
img_r, _, _ = R.build_step("living", [inst[k]["id"] for k in READ], read_op, "t-world-read")
ACT = [("cover.move", "curtain"), ("climate.set", "aircon")]
act_op = f'void op_main(void){{ if (cap_cover_move({K(*ACT[0])},-1) || cap_climate_set({K(*ACT[1])},1,24)) {{ fail("a"); return; }} say("ok"); }}'
img_a, _, allow = R.build_step("living", [inst[k]["id"] for k in ACT], act_op, "t-world-act")

b0 = R.boot_step("living", img_r); r0 = b0.get("result") or {}
check("read-before", r0.get("ac_on") == 0 and r0.get("closed") == 0 and abs(r0.get("mC", 0) - 27000) <= 63 and not b0["world"]["changed"], json.dumps(r0))
b1 = R.boot_step("living", img_a)
ch = b1["world"]["changed"]
check("act-reaches-the-world", ch.get("living/curtain", {}).get("now") == "closed" and ch.get("living/aircon", {}).get("now") == {"on": 1, "target": 24}, json.dumps(ch, ensure_ascii=False))
check("act-allow-list-is-the-two-expanders", sorted(d["addr"] for d in allow["devices"]) == [0x20, 0x21], str([d["addr"] for d in allow["devices"]]))
b2 = R.boot_step("living", img_r); r2 = b2.get("result") or {}
check("read-back-after-act", r2.get("ac_on") == 1 and r2.get("ac_t") == 24 and r2.get("closed") == 1 and r2.get("light") == 0 and not b2["world"]["changed"], json.dumps(r2))
w = W.load(); W.advance(w, 60); W.save(w)
b3 = R.boot_step("living", img_r); r3 = b3.get("result") or {}
check("room-cools-with-the-aircon", r3.get("mC", 99999) <= 25200 and r3.get("mC", 0) >= 24000, f"{r0.get('mC')} → {r3.get('mC')} m°C after 60 sim-min")

# the rules alone: west sun through an open curtain makes the evening hot; closed, much less
peak = {}
for c in ("open", "closed"):
    w = W.fresh("13:00"); w["act"]["living/curtain"] = c; W.advance(w, 330); peak[c] = w["room"]["living"]
check("west-sun-rule", peak["open"] - peak["closed"] >= 2.5, json.dumps({k: round(v, 2) for k, v in peak.items()}))

# plans: an action cannot repeat; a standing intent must watch with a reading step and name a condition
probs = R.validate_plan({"steps": [{"id": "a1", "on": "living", "uses": [inst[ACT[1]]["id"]], "do": "x", "repeat": {"every_min": 10, "times": 3}}]})
check("repeat-only-for-reads", any("only reading steps" in p for p in probs), "; ".join(probs))
good = {"watch": {"id": "w1", "on": "living", "uses": [inst[("motion.read", "motion")]["id"]], "do": "motion"}, "every_min": 10, "until_min": 600,
        "when": {"key": "motion", "op": "==", "value": 0, "times": 3}, "then": [{"id": "a1", "on": "living", "uses": [inst[ACT[1]]["id"]], "do": "off"}]}
bad = {**good, "watch": {**good["watch"], "uses": [inst[ACT[1]]["id"]]}, "when": {"key": "motion", "op": "~", "value": 0}}
pb = R.validate_standing(bad)
check("standing-checked", not R.validate_standing(good) and any("may only read" in p for p in pb) and any("standing.when" in p for p in pb), "; ".join(pb))

print(f"world {'PASS' if ok == n else 'FAIL'} {ok}/{n}")
sys.exit(0 if ok == n else 1)
