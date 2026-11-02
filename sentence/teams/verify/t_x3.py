"""X3: a known part wired away from its catalog address. With wiring.json {"tmp105": [0x4a]} the image's
allow list names 0x4a, an op reading 0x4a works and one reading the catalog address 0x48 is stopped.
Uses a private wiring file and a bench with the TMP105 at 0x4a (the real sentence/wiring.json is untouched).
    ../sim/body/.venv/bin/python teams/verify/t_x3.py"""
import os, sys, json, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import tempfile; os.environ["POKE_RECORDS"] = tempfile.mkdtemp(prefix="verify-records-")   # never the owner's records
import say
w = os.path.join(tempfile.mkdtemp(), "wiring.json")
json.dump({"tmp105": ["0x4a"]}, open(w, "w"))
say.WIRING = w
say.BENCH = [(m, q, 0x4a if q == "t1" else a) for m, q, a in say.BENCH]
SRC = '#include "rt.h"\n#include "tmp105.h"\nvoid op_main(void){ long t; int e = tmp105_read_mC(%s, &t); if (e) { report_int("e", e); fail("tmp105"); return; } report_int("mC", t); }'
good = 0
_, _, p = say.build(SRC % "0x4a", "vt-x3-4a")
side = json.load(open(p[:-4] + ".i2c.json"))
ok = [d["addr"] for d in side["allowlist"]["devices"]] == [0x4a] and side["wired"] == {"tmp105": [0x4a]} and side["fn_addr"]["tmp105_read_mC"] == [0x4a]
print(f"{'✓' if ok else '✗'} allow list moved to 0x4a: {side['allowlist']['devices']} wired={side['wired']}"); good += ok
b = say.boot(p, {"t1 temperature": 21000})
r = say.effects_report(b, p)
ok = b["result"] == {"mC": 21000} and r["ok"] and not r["undeclared"]
print(f"{'✓' if ok else '✗'} op at 0x4a reads the part: {b['result']} effects ok={r['ok']}"); good += ok
_, _, p = say.build(SRC % "0x48", "vt-x3-48")
b = say.boot(p, {"t1 temperature": 21000})
f = next((l for l in b["lines"] if l.startswith("POKE-FAULT")), "")
ok = "kind=i2c" in f and "not in its manifest" in (b["result"] or {}).get("error", "")
print(f"{'✓' if ok else '✗'} op at the catalog address 0x48 is stopped: {f}"); good += ok
say.WIRING = os.path.join(tempfile.mkdtemp(), "none.json")
_, _, p = say.build(SRC % "0x48", "vt-x3-none")
ok = [d["addr"] for d in json.load(open(p[:-4] + ".i2c.json"))["allowlist"]["devices"]] == [0x48]
print(f"{'✓' if ok else '✗'} no wiring file → catalog address"); good += ok
print(f"\n{good}/4 wiring cases as expected"); sys.exit(0 if good == 4 else 1)
