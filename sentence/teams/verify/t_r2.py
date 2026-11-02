"""R2: when a part moves (wiring.json), an op that names its address as POKE_ADDR_<FRAGMENT> is rebuilt with
the new address (no model call) and its allow list follows; an old op with a numeric address is reported
stale for exactly the moved part (say.stale_addresses). Private wiring file; TMP105 moved to 0x4a on the bench.
    ../sim/body/.venv/bin/python teams/verify/t_r2.py"""
import os, sys, json, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import tempfile; os.environ["POKE_RECORDS"] = tempfile.mkdtemp(prefix="verify-records-")   # never the owner's records
import say, hub
MACRO = '#include "rt.h"\n#include "tmp105.h"\n#define POKE_ADDR_TMP105 0x48\nvoid op_main(void){ long t; if (tmp105_read_mC(POKE_ADDR_TMP105, &t)) { fail("tmp105"); return; } report_int("mC", t); }'
LITERAL = '#include "rt.h"\n#include "tmp105.h"\n#include "led.h"\nvoid op_main(void){ long t; if (tmp105_read_mC(0x48, &t)) { fail("tmp105"); return; } led_set(0x60, 1, 0); report_int("mC", t); }'
good = 0
w = os.path.join(tempfile.mkdtemp(), "wiring.json"); json.dump({"tmp105": ["0x4a"]}, open(w, "w")); say.WIRING = w
say.BENCH = [(m, q, 0x4a if q == "t1" else a) for m, q, a in say.BENCH]
src = hub.with_hub_consts(MACRO)
import re
m = re.search(r"#define POKE_ADDR_TMP105\s+(\S+)", src); ok = bool(m) and int(m.group(1), 0) == 0x4a
print(f"{'✓' if ok else '✗'} hub constant rewritten to the wired address: {[l for l in src.splitlines() if 'POKE_ADDR' in l][:1]}"); good += ok
_, _, p = say.build(src, "vt-r2-macro")
b = say.boot(p, {"t1 temperature": 19500})
ok = b["result"] == {"mC": 19500} and say.effects_report(b, p)["ok"] and [d["a"] for d in b["effects"]["dev"]] == [0x4a]
print(f"{'✓' if ok else '✗'} same op source, moved part: reads 0x4a → {b['result']}"); good += ok
st = say.stale_addresses(LITERAL)
ok = st == [("tmp105_read_mC", 0x48)]
print(f"{'✓' if ok else '✗'} numeric-address op: stale = {st} (the LED at 0x60 did not move, so it is not listed)"); good += ok
ok = say.stale_addresses(MACRO) == []
print(f"{'✓' if ok else '✗'} POKE_ADDR op is never stale"); good += ok
print(f"\n{good}/4 wiring-change cases as expected"); sys.exit(0 if good == 4 else 1)
