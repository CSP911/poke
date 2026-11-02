"""동시 빌드 경합 재현: 같은 문장의 make_op를 스레드 4개가 동시에(같은 키 태그로 build) 20회.
    ../../../sim/body/.venv/bin/python t_race.py   (sentence/ 에서: ../sim/body/.venv/bin/python teams/test/t_race.py)"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import isolate  # noqa: E402,F401 — 소유자 기록 보호(결정 7): POKE_RECORDS를 임시 사본으로
import os, sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import say
errs, ok = [], 0
def one(_):
    try:
        op = say.make_op("온도 알려줘", log=lambda *a: None)
        b = say.boot(op["path"], say.default_bench(), budget_s=8)
        return "ok" if b["powered_off"] and (b["result"] or {}).get("temp_c") else f"bad {b['result']}"
    except Exception as e:
        return f"{type(e).__name__}: {' '.join(str(e).split())[:120]}"
with ThreadPoolExecutor(4) as ex:
    rs = list(ex.map(one, range(20)))
print(f"{rs.count('ok')}/20 ok"); [print(" ", r) for r in sorted(set(rs)) if r != "ok"]
