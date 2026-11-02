"""장치 부재: 실험대에서 장치를 빼거나 옮기고(say.BENCH를 이 프로세스 안에서만 바꿈) 그 장치를 쓰는 캐시 op를 부팅.
    ../../../sim/body/.venv/bin/python t_absent.py"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import isolate  # noqa: E402,F401 — 소유자 기록 보호(결정 7): POKE_RECORDS를 임시 사본으로
import os
from common import say, build_tt, save

FULL = list(say.BENCH)
def without(model):
    return [b for b in FULL if b[0] != model]
def moved(model, addr):
    return [(m, q, addr if m == model else a) for m, q, a in FULL]

CASES = [
    ("온도 알려줘 / TMP105 없음", "600ad0ab6d86", without("tmp105"), {}),
    ("온도 알려줘 / TMP105를 0x4a로 옮김", "600ad0ab6d86", moved("tmp105", 0x4a), {"t1 temperature": 23750}),
    ("LED 3번 켜줘 / PCA9552 없음", "40c84902bd30", without("pca9552"), {}),
    ("25도 넘으면 LED 0 / PCA9552 없음, 27.25도", "eba0bb1b8762", without("pca9552"), {"t1 temperature": 27250}),
    ("25도 넘으면 LED 0 / TMP105 없음", "eba0bb1b8762", without("tmp105"), {}),
    ("지금 몇 시야 / DS1338 없음", "bc0e2112cd8f", without("ds1338"), {"t1 temperature": 23750}),
    ("자기장 / 실험대 비움", "785d3c5a0df8", [], {}),
]
out = []
for i, (name, key, bench_devs, state) in enumerate(CASES):
    src = open(os.path.join(say.BUILD, "ops", key + ".c")).read()
    _, _, path, chk = build_tt(src, f"tt-absent-{key}")
    say.BENCH = bench_devs
    try:
        b = say.boot(path, state, budget_s=10)
        row = {"name": name, "result": b["result"], "off": b["powered_off"], "node_ms": b["node_ms"], "boot_to_off_ms": b["boot_to_off_ms"],
               "say": [l for l in b["lines"] if l.startswith(("POKE-SAY", "POKE-FAULT"))], "truth": b["truth"]}
    except Exception as e:
        row = {"name": name, "hub_exception": repr(e)[:200]}
    finally:
        say.BENCH = FULL
    out.append(row)
    print(f"[{i}] {name:<40} off={row.get('off')} node_ms={row.get('node_ms')} result={row.get('result')} say={row.get('say')} {row.get('hub_exception', '')}")
save("absent", out)
