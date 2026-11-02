"""조각 경계값: TMP105 음수·극단 온도를 조각이 바르게 읽는가(selftest는 21.5도 하나뿐).
    ../../../sim/body/.venv/bin/python t_frag_edge.py"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import isolate  # noqa: E402,F401 — 소유자 기록 보호(결정 7): POKE_RECORDS를 임시 사본으로
from common import say, build_tt, save, H

SRC = H + '#include "tmp105.h"\nvoid op_main(void){ long t; int e = tmp105_read_mC(0x48, &t); if (e) { report_int("e", e); fail("tmp105"); return; } report_int("mC", t); report_fixed("c", t, 3); }'
_, _, path, chk = build_tt(SRC, "tt-frag-tmp105")
out = []
for truth in (-55000, -10250, -500, -62, 0, 62, 25250, 124937, 128000):
    try:
        b = say.boot(path, {"t1 temperature": truth}, budget_s=6)
        r = b["result"] or {}
        out.append({"set_mC": truth, "truth_after": b["truth"].get("temperature_mC"), "read_mC": r.get("mC"), "c": r.get("c"), "err": r.get("error"),
                    "diff": None if r.get("mC") is None else r["mC"] - b["truth"].get("temperature_mC", truth)})
    except Exception as e:
        out.append({"set_mC": truth, "exc": repr(e)[:150]})
    print(out[-1])
save("frag_edge", out)
