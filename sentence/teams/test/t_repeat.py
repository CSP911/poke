"""반복 안정성: 캐시된 op 두 개를 각각 N번 연속 부팅. 매번 꺼지는지, 결과가 같은지, 시간 분포.
    ../../../sim/body/.venv/bin/python t_repeat.py [N]"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import isolate  # noqa: E402,F401 — 소유자 기록 보호(결정 7): POKE_RECORDS를 임시 사본으로
import sys
from common import say, build_tt, summary, save, qemu_leftovers
import os

N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
CASES = [
    ("온도 알려줘 (600ad0ab6d86)", "600ad0ab6d86", {"t1 temperature": 23750},
     lambda r, t: r and abs(r.get("temperature_c", -99) - 23.5) <= 0.5),
    ("LED 0~3 0.2s 간격 (4b91f05b8a15)", "4b91f05b8a15", {},
     lambda r, t: t.get("leds_on") == [0, 1, 2, 3] and r and not r.get("error")),
]
out = {}
for name, key, bench, good in CASES:
    src = open(os.path.join(say.BUILD, "ops", key + ".c")).read()
    img, obj, path, chk = build_tt(src, f"tt-repeat-{key}")
    rows = []
    for i in range(N):
        b = say.boot(path, bench)
        ok = bool(b["powered_off"]) and good(b["result"], b["truth"])
        rows.append({"i": i, "ok": ok, "off": b["powered_off"], "node_ms": b["node_ms"], "boot_to_off_ms": b["boot_to_off_ms"],
                     "qemu_total_ms": b["qemu_total_ms"], "result": b["result"], "vm": b["vm_status"]})
        if not ok:
            print("  FAIL", i, b["result"], b["vm_status"], b["lines"][-5:])
    leftovers = qemu_leftovers()
    out[name] = {"n": N, "ok": sum(r["ok"] for r in rows), "off": sum(r["off"] for r in rows),
                 "distinct_results": len({str(r["result"]) for r in rows}),
                 "boot_to_off_ms": summary([r["boot_to_off_ms"] for r in rows]),
                 "qemu_total_ms": summary([r["qemu_total_ms"] for r in rows]),
                 "node_ms": summary([r["node_ms"] for r in rows]), "qemu_left_running": leftovers, "rows": rows}
    o = out[name]
    print(f"{name}: ok {o['ok']}/{N}, off {o['off']}/{N}, distinct results {o['distinct_results']}, "
          f"boot→off {o['boot_to_off_ms']}, qemu total {o['qemu_total_ms']}, node_ms {o['node_ms']}, leftover qemu {leftovers}")
save("repeat", out)
