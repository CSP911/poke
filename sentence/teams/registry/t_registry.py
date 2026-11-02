#!/usr/bin/env python3
"""The body registry (L0–L3) and capability-API images, without a model: a granted capability boots and reads
what the bench holds; a plan naming a missing, foreign or denied capability is refused; an op that uses a
capability its step was not granted does not compile (the header holds only what was granted)."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); SENT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, SENT)
import registry as R

ok, n = 0, 0
def check(name, cond, detail=""):
    global ok, n
    n += 1; ok += bool(cond)
    print(f"{'PASS' if cond else 'FAIL'} {name} {detail}")

ids = ["bedroom/i2c1/0x4c#temperature.read@local", "bedroom/i2c1/0x4c#temperature.read@remote"]
op = '''void op_main(void){ long a,b; if (cap_temperature_read(CAP_BEDROOM_TEMPERATURE_LOCAL,&a)||cap_temperature_read(CAP_BEDROOM_TEMPERATURE_REMOTE,&b)){fail("r");return;}
 report_int("a",a); report_int("b",b); }'''
img, src, allow = R.build_step("bedroom", ids, op, "t-reg-read")
b = R.boot_step("bedroom", img, {"x1 temperature0": 21000, "x1 temperature1": 33500})
r = b.get("result") or {}
check("cap-read-matches-bench", abs(r.get("a", 0) - 21000) <= 63 and abs(r.get("b", 0) - 33500) <= 63 and b["powered_off"], str(r))
check("allow-list-is-the-granted-part-only", [d["addr"] for d in allow["devices"]] == [0x4c] and allow["devices"][0]["write_regs"] == [], str(allow))
check("chip-names-not-in-constants", "TMP421" not in " ".join(i["const"] for i in R.instances()), "")

bad = {"steps": [{"id": "s1", "on": "living", "uses": ["living/i2c1/0x99#temperature.read"], "do": "x"},
                 {"id": "s2", "on": "living", "uses": ["bedroom/i2c1/0x4c#temperature.read@local"], "do": "x"}]}
probs = R.validate_plan(bad)
check("plan-missing-and-foreign-refused", any("does not exist" in p for p in probs) and any("not living" in p for p in probs), "; ".join(probs))
R_parts = R.parts()
denied_any = [i["id"] for i in R.instances(include_denied=True) if i["policy"] == "deny"]
if denied_any:
    i0 = next(i for i in R.instances(include_denied=True) if i["policy"] == "deny")
    probs = R.validate_plan({"steps": [{"id": "s1", "on": i0["node"], "uses": [i0["id"]], "do": "x"}]})
    check("plan-denied-refused", any("denied" in p for p in probs), "; ".join(probs))
else:
    print("SKIP plan-denied-refused (no denied capability on these nodes)")

try:
    R.build_step("bedroom", ids[:1], 'void op_main(void){ int on; cap_switch_read(CAP_BEDROOM_SWITCH_0,&on); report_int("on",on); }', "t-reg-ungranted")
    check("ungranted-capability-does-not-compile", False, "it compiled")
except RuntimeError as e:
    check("ungranted-capability-does-not-compile", True, str(e).splitlines()[-1][:80])

print(f"registry {'PASS' if ok == n else 'FAIL'} {ok}/{n}")
sys.exit(0 if ok == n else 1)
