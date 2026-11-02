"""5차 감사 2부: 학습 고리(learn.py → incubation.py → register). Model-free: no agent, no claude -p.
Booted cases use a private POKE_INCUBATED / POKE_RECORDS; nothing of the owner's is touched.
    ../sim/body/.venv/bin/python teams/verify/t_learn.py
FINDING = a gap the audit found (fails until the owning team fixes it)."""
import os, sys, json, inspect, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
os.environ["POKE_RECORDS"] = tempfile.mkdtemp(prefix="verify-records-")
os.environ["POKE_INCUBATED"] = tempfile.mkdtemp(prefix="verify-inc-")
import say, probe, incubation, learn
good = n = 0
def chk(name, ok, finding=False):
    global good, n; n += 1; good += bool(ok)
    print(f"{'✓' if ok else '✗'} {'FINDING ' if finding and not ok else ''}{name}")

# 1 scan is read-only (R3): no pointer, no write, at any address
a = say.scan_allowlist([0x48, 0x60])
chk("scan: 읽기만(포인터·쓰기 0)", all(d["pointer"] is False and d["write_regs"] == [] for d in a["devices"]))
src = inspect.getsource(probe.scan)
chk("probe.scan이 scan_allowlist를 씀", "scan_allowlist" in src)
# 2 identify: the agent's only tool is probe.py — can it write to a part the hub already knows?
al = probe.allowlist([("w", 0x60, [0x06, 0x00])])                 # LS0 of the known LED expander
known = incubation.known_addrs()
chk("identify의 probe가 이미 아는 부품(0x60 LED)에 쓰기를 허용하지 않음", not any(d["addr"] in known and d["write_regs"] for d in al["devices"]), finding=True)
# 3 verification images: allow list = exactly the declared write registers of the part, one device
s = inspect.getsource(incubation.run_test)
chk("검증 이미지 허용 목록 = 그 주소 하나, 선언된 쓰기 레지스터만", '"devices": [{"addr": addr' in s and "write_regs" in s)
chk("검증 이미지를 build(allow=)로 원자 공개(뒤에서 다시 쓰지 않음)", 'open(img, "wb").write(say.patch_i2c_allow' not in s, finding=True)
s = inspect.getsource(incubation.run_identify)
chk("identify 시험: 모든 주소 읽기+포인터만, 쓰기 0", '"write_regs": []' in s and "allow=allow" in s)
# 4 V7 review cannot be skipped
s = inspect.getsource(incubation.register)
chk("register()가 incubation_review 결과로 FN을 씀", "incubation_review" in s and 'rec["functions"] = [reviewed' in s)
s = inspect.getsource(incubation.amend_identify)
chk("amend_identify: 추가 함수는 읽기 전용으로 강제(쓰기 0, allow)", '"access": "read", "write_regs": []' in s)
# reconcile_orphans only takes back (chair's decision): allowed here, and pinned by the test below
other = [f for f, o in inspect.getmembers(incubation, inspect.isfunction) if "FN_JSON" in inspect.getsource(o) and f not in ("register", "amend_identify", "reconcile_orphans")]
chk(f"fn.json을 쓰는 다른 경로 없음 ({other or '없음'}; reconcile_orphans는 회수 전용으로 허용)", not other)
import shutil
tmp = tempfile.mkdtemp(prefix="verify-reconcile-")
cat_tmp, frags_tmp = os.path.join(tmp, "catalog.json"), os.path.join(tmp, "frags")
os.makedirs(frags_tmp); shutil.copy(incubation.CAT, cat_tmp)
fn0 = {"FN": {"kept_read": {"frag": "kept", "addr": 0x50, "access": "read", "write_regs": [], "max_write": 1},
              "half_set": {"frag": "half", "addr": 0x51, "access": "write", "write_regs": [3], "max_write": 2}},
       "POLICY": {"kept_read": "allow", "half_set": "confirm"}}
fn_tmp = os.path.join(tmp, "fn.json"); json.dump(fn0, open(fn_tmp, "w"))
cat0 = json.load(open(cat_tmp)); cat0["kept"] = {"name": "kept"}; json.dump(cat0, open(cat_tmp, "w"))
open(os.path.join(os.environ["POKE_INCUBATED"], "kept.json"), "w").write("{}")
os.makedirs(os.path.join(os.environ["POKE_INCUBATED"], "pending"), exist_ok=True)
for nm in ("kept", "half"):
    json.dump({"name": nm, "pid": 1}, open(os.path.join(os.environ["POKE_INCUBATED"], "pending", nm + ".json"), "w"))
open(os.path.join(frags_tmp, "half.h"), "w").write("/* half */")
saved = (incubation.CAT, incubation.FN_JSON, incubation.INC, say.FRAGS)
incubation.CAT, incubation.FN_JSON, incubation.INC, say.FRAGS = cat_tmp, fn_tmp, os.environ["POKE_INCUBATED"], frags_tmp
try:
    incubation.reconcile_orphans()
finally:
    incubation.CAT, incubation.FN_JSON, incubation.INC, say.FRAGS = saved
fn1, cat1 = json.load(open(fn_tmp)), json.load(open(cat_tmp))
only_removes = set(fn1["FN"]) <= set(fn0["FN"]) and all(fn1["FN"][k] == fn0["FN"][k] and fn1["POLICY"].get(k) == fn0["POLICY"].get(k) for k in fn1["FN"]) \
               and set(cat1) <= set(cat0)
chk("reconcile_orphans: 반쯤 등록된 부품만 회수, 완전한 부품은 그대로, 추가·확장 0", only_removes and "half_set" not in fn1["FN"] and "kept_read" in fn1["FN"]
    and not os.path.exists(os.path.join(frags_tmp, "half.h")))
# 5 half-registered state: order and atomicity
s = inspect.getsource(incubation.register)
i_h, i_f, i_c = s.find("os.replace(tmp, hp)"), s.find("write_json_atomic(FN_JSON"), s.find("write_json_atomic(CAT")
chk("등록 순서: 헤더 → FN → 카탈로그 (카탈로그가 마지막 = 보이는 순간 완전)", 0 < i_h < i_f < i_c)
chk("중간에 죽으면 남는 헤더·FN을 치우는 정리(카탈로그에 없는 FN/헤더 회수)", any("orphan" in inspect.getsource(o) or "reconcile" in inspect.getsource(o)
     for _, o in inspect.getmembers(incubation, inspect.isfunction)), finding=True)
# 6 two addresses at once
s = inspect.getsource(learn.start)
chk("learn.start: 같은 주소는 한 작업", "j.get(\"addr\") ==" in s)
s = inspect.getsource(incubation.identify)
chk("두 작업이 동시에 돌 때 identify 기록이 서로 덮이지 않음(작업별 경로)", 'os.path.join(STAGE, "identify.json")' not in s, finding=True)
s = inspect.getsource(learn.start)
chk("다른 주소 두 작업을 동시에 허용하지 않음(실기기는 한 번에 한 부팅)", "running" in s and "addr" in s and "any(" in s, finding=True)
print(f"\n{good}/{n} learning-loop cases as expected"); sys.exit(0 if good == n else 1)
