"""V7: incubation safety review (say.incubation_review). A synthetic record checks the rules; then the four
registered parts are reviewed retroactively → teams/verify/v7-review.json (proposed FN/POLICY, not applied).
    ../sim/body/.venv/bin/python teams/verify/t_v7.py"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import tempfile; os.environ["POKE_RECORDS"] = tempfile.mkdtemp(prefix="verify-records-")
import say
fx = lambda regs, n: "POKE-EFFECTS2 " + __import__("struct").pack("<IIBBBB", 1, 0, 1, 1, 32, 2).hex() + \
     (__import__("struct").pack("<IIIBB", 1, 0, 0, 0x20, 1) + bytes(sum(1 << (r & 7) << 8 * (r >> 3) for r in regs).to_bytes(32, "little")) + bytes(32) + bytes(2)).hex() + \
     __import__("struct").pack("<IBBBBI8s", 0, 0x20, 0, 0, 2, n, b"\x01\x00" + bytes(6)).hex()
REC = lambda conf: {"addr": "0x20", "confidence": conf,
    "functions": [{"sig": "static int x_set(unsigned a, int v)", "access": "write", "write_regs": [1, 3, 5], "max_write": 8},
                  {"sig": "static int x_read(unsigned a, int *v)", "access": "read", "write_regs": [], "max_write": 1},
                  {"sig": "static int x_spare(unsigned a)", "access": "write", "write_regs": [9], "max_write": 2}],
    "tests": [{"name": "t1"}], "test_ops": ['void op_main(void){ x_set(0x20, 1); x_read(0x20, 0); }'],
    "verification": [{"test": "t1", "draws": [{"lines": [fx([1], 2)]}, {"lines": [fx([1, 3], 2)]}]}]}
good = n = 0
def chk(name, ok):
    global good, n; n += 1; good += bool(ok); print(f"{'✓' if ok else '✗'} {name}")
r = say.incubation_review(REC(0.9)); f = {x["fn"]: x for x in r["rows"]}
chk("keeps only the write registers seen ([1,3,5] → [1,3])", f["x_set"]["kept"] == [1, 3])
chk("max_write shrinks to the longest write seen (8 → 2)", f["x_set"]["max_write"] == [8, 2])
chk("a write function no test calls is denied, nothing kept", f["x_spare"]["policy"][1] == "deny" and f["x_spare"]["kept"] == [])
chk("confident (90%) write function stays 'confirm'", f["x_set"]["policy"][1] == "confirm")
rf = REC(0.9); rf["verification"].append({"test": "t1", "ok": False, "draws": [{"lines": [fx([1, 3, 5], 2)]}]})
f2 = {x["fn"]: x for x in say.incubation_review(rf)["rows"]}
chk("실패한 검증 실행의 쓰기는 근거가 아님 (5는 실패 실행에서만 관측 → 버림)", f2["x_set"]["kept"] == [1, 3])
r = say.incubation_review(REC(0.7)); f = {x["fn"]: x for x in r["rows"]}
chk("identified at 70% → write functions denied", f["x_set"]["policy"][1] == "deny" and f["x_read"]["policy"][1] == "allow")
out, table = {"FN": {}, "POLICY": {}}, []
for name in ("tmp421", "pca9554", "ssd0303", "dps310"):
    p = os.path.join(say.HERE, "incubated", f"{name}.json")
    if not os.path.exists(p): continue
    rec = json.load(open(p)); rv = say.incubation_review(rec)
    for row, fnd in zip(rv["rows"], rv["functions"]):
        table.append({"part": name, "confidence": rv["confidence"], **row})
        out["FN"][row["fn"]] = {"write_regs": fnd["write_regs"], "max_write": fnd["max_write"]}
        out["POLICY"][row["fn"]] = fnd["policy"]
json.dump({"proposed": out, "table": table}, open(os.path.join(HERE, "v7-review.json"), "w"), indent=1, ensure_ascii=False)
print(f"\n{good}/{n} review rules as expected; retro review of {len({t['part'] for t in table})} parts → teams/verify/v7-review.json")
for t in table:
    print(f"  {t['part']:<8} {t['confidence']:.2f} {t['fn']:<18} {t['access']:<5} declared {t['declared']} seen {t['seen']} → kept {t['kept']} max_write {t['max_write'][0]}→{t['max_write'][1]} policy {t['policy'][0]}→{t['policy'][1]} {'; '.join(t['why'])}")
sys.exit(0 if good == n else 1)
