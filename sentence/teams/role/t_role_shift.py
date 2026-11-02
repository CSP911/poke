#!/usr/bin/env python3
"""변환 · 시간/역할: the same node, the same parts, a different role by time of day — "낮에는 불량품 검사, 밤에는
설비 변화 관측". Factory site (POKE_SITE=factory), node line1: a test jig's power line (ADM1272), machine
temperatures (EMC1413: control box, spindle bearing, gearbox), an I/O board (reject gate, item sensor).

    day   · inspect  grants power.measure + motion.read@item + switch.set@reject_gate — an item on the jig whose
                     current is outside 1.5–2.5 A is pushed out by the reject gate
    night · observe  grants temperature.read ×3 + power.measure — reads only; the gate is not in its body

What must hold across the shift (model-free, the ops are written here):
  R1 day role judges items correctly (good / short / empty jig), the gate pin is the truth
  R2 night role cannot act: the reject gate does not compile in its image, its allow list writes nothing
  R3 no residue: day then night on the same node — each boot's memory is zero after reset, the images differ
  R4 return: tomorrow's day image is today's, byte for byte (same role → same pronunciation)
  R5–R7 not built yet (XFAIL, the reason printed): the hub choosing the role by the clock, a request that
        straddles the boundary, per-role baselines (night trends not polluted by day production heat)
"""
import os, sys, hashlib
HERE = os.path.dirname(os.path.abspath(__file__)); SENT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, SENT)
os.environ["POKE_SITE"] = "factory"
os.environ.pop("POKE_WORLD", None)                 # the home's world has no factory
import registry as R

ok = n = 0
def check(name, cond, detail=""):
    global ok, n
    n += 1; ok += bool(cond)
    print(f"{'PASS' if cond else 'FAIL'} {name} {detail}")
def xfail(name, why):
    print(f"XFAIL {name} — {why}")

I = {i["id"].split("#", 1)[1]: i for i in R.instances("line1")}
C = lambda k: I[k]["const"]
ids = lambda ks: [I[k]["id"] for k in ks]

DAY = ["power.measure", "motion.read@item", "switch.set@reject_gate"]
NIGHT = ["temperature.read@internal", "temperature.read@remote1", "temperature.read@remote2", "power.measure"]
DAY_OP = f'''void op_main(void) {{
    int present; long v, i, p;
    if (cap_motion_read({C("motion.read@item")}, &present)) {{ fail("item sensor"); return; }}
    if (!present) {{ report_str("verdict", "empty"); return; }}
    if (cap_power_measure({C("power.measure")}, &v, &i, &p)) {{ fail("jig power"); return; }}
    int bad = i < 1500 || i > 2500;
    if (cap_switch_set({C("switch.set@reject_gate")}, bad)) {{ fail("gate"); return; }}
    report_int("iout_ma", i); report_str("verdict", bad ? "reject" : "pass");
}}
'''
NIGHT_OP = f'''void op_main(void) {{
    long a, b, c, v, i, p;
    if (cap_temperature_read({C("temperature.read@internal")}, &a) || cap_temperature_read({C("temperature.read@remote1")}, &b)
        || cap_temperature_read({C("temperature.read@remote2")}, &c)) {{ fail("temps"); return; }}
    if (cap_power_measure({C("power.measure")}, &v, &i, &p)) {{ fail("idle power"); return; }}
    report_int("box_mC", a); report_int("bearing_mC", b); report_int("gearbox_mC", c); report_int("idle_mw", p);
}}
'''
def jig(ma, present=True):
    return {"c5 vin": 24000, "c5 vout": 23960, "c5 iout": ma, "c5 pin": 24 * ma // 1000, "g1 pin4": "high" if present else "low", "g1 pin0": "low"}
def gate(b):
    return (((b.get("truth") or {}).get("ext") or {}).get("pins") or {}).get("g1", {}).get(0)
def sha(p): return hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]

day_img, _, day_allow = R.build_step("line1", ids(DAY), DAY_OP, "t-role-day")
night_img, _, night_allow = R.build_step("line1", ids(NIGHT), NIGHT_OP, "t-role-night")

# R1 — day: good / short / empty
cases = [("good", jig(2000), "pass", "low"), ("short", jig(3800), "reject", "high"), ("empty", jig(0, present=False), "empty", "low")]
for name, st, want, want_gate in cases:
    b = R.boot_step("line1", day_img, st); r = b.get("result") or {}
    check(f"R1-day-{name}", r.get("verdict") == want and gate(b) == want_gate and b.get("powered_off"),
          f"verdict={r.get('verdict')} iout={r.get('iout_ma')} gate={gate(b)}")

# R2 — night cannot act
try:
    R.build_step("line1", ids(NIGHT), NIGHT_OP.replace('report_int("idle_mw", p);', f'report_int("idle_mw", p); cap_switch_set({C("switch.set@reject_gate")}, 1);'), "t-role-night-gate")
    check("R2-night-gate-does-not-compile", False, "an image with the gate built")
except RuntimeError as e:
    check("R2-night-gate-does-not-compile", True, str(e).strip().splitlines()[-1][:90])
writes = [d for d in night_allow["devices"] if d.get("write_regs") and d["addr"] == 0x20]
check("R2-night-allow-list-writes-nothing-on-the-io-board", not writes and 0x20 not in [d["addr"] for d in night_allow["devices"]],
      str([(d["addr"], d.get("write_regs")) for d in night_allow["devices"]]))

# R3 — day then night on the same node: no residue
bd = R.boot_step("line1", day_img, jig(3800))
night_st = {"c1 temperature0": 31000, "c1 temperature1": 58500, "c1 temperature2": 47000, **jig(40, present=False)}
bn = R.boot_step("line1", night_img, night_st); rn = bn.get("result") or {}
wd, wn = (bd.get("wipe_check") or {}), (bn.get("wipe_check") or {})
check("R3-night-reads-the-machine", rn.get("bearing_mC") is not None and abs(rn["bearing_mC"] - 58500) <= 750 and "verdict" not in rn, str(rn))
check("R3-memory-zero-after-each-role", wd.get("nonzero") == 0 and wn.get("nonzero") == 0, f"day {wd.get('nonzero')}/{wd.get('bytes')} night {wn.get('nonzero')}/{wn.get('bytes')}")
check("R3-two-roles-two-images", sha(day_img) != sha(night_img), f"{sha(day_img)} vs {sha(night_img)}")

# R4 — tomorrow's day image is today's
day2, _, _ = R.build_step("line1", ids(DAY), DAY_OP, "t-role-day-tomorrow")
check("R4-same-role-same-bytes", sha(day2) == sha(day_img), f"{sha(day_img)} vs {sha(day2)}")

xfail("R5-hub-picks-the-role-by-the-clock", "노드에 역할 일정(modes: 시간대 → 허용 능력)이 아직 없음 — 지금은 계획이 직접 고름")
xfail("R6-request-straddling-the-boundary", "19:59에 시작해 20:00을 넘는 요청: 낮 권한으로 끝내기 / 거절 규칙 미정")
xfail("R7-per-role-baselines", "밤 관측 추이가 낮 생산 열을 기준선에 섞지 않게 — 역할별 기록 분리 미구현")

print(f"role shift {'PASS' if ok == n else 'FAIL'} {ok}/{n} (+3 xfail)")
sys.exit(0 if ok == n else 1)
