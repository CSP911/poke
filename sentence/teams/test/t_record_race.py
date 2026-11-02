"""기록 동시 접근 재현: 기억된 문장 make_op를 스레드 8개로 400회 — 기록 JSON 읽기/쓰기 경합(JSONDecodeError 등)을 센다.
    cd sentence && ../sim/body/.venv/bin/python teams/test/t_record_race.py"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import isolate  # noqa: E402,F401 — 소유자 기록 보호(결정 7): POKE_RECORDS를 임시 사본으로
import os, sys, traceback, collections
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))); os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import say, claude_cli
claude_cli.ask = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no model"))
S = ["온도 알려줘", "LED 3번 켜줘", "LED 12번 켜줘", "지금 몇 시야?", "LED 1번이랑 2번 켜줘"]
def one(i):
    try: say.make_op(S[i % len(S)], log=lambda *a: None); return "ok"
    except Exception as e:
        tb = traceback.extract_tb(e.__traceback__); f = next((x for x in reversed(tb) if "sentence" in x.filename), tb[-1])
        return f"{type(e).__name__}: {str(e)[:60]} @ {os.path.basename(f.filename)}:{f.lineno} {f.name}"
with ThreadPoolExecutor(8) as ex: rs = list(ex.map(one, range(400)))
c = collections.Counter(rs); print(c["ok"], "/ 400 ok"); [print(" ", n, k) for k, n in c.items() if k != "ok"]
