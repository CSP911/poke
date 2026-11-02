"""record.save 경합 재현: 같은 기록에 '들은 말'(heard)을 스레드 8개·프로세스 2개가 동시에 추가 — 기록 파일이 깨지거나 말이 사라지는지.
본 기록을 건드리지 않게 build/records의 한 기록을 임시 POKE_RECORDS로 복사해서 씀.
    cd sentence && ../sim/body/.venv/bin/python teams/test/t_record_save_race.py"""
import os, sys, json, shutil, tempfile, subprocess
SENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if "--child" not in sys.argv:
    d = tempfile.mkdtemp(prefix="recrace-")
    src = os.path.join(SENT, "build", "records", "r-9155ba1300.json"); shutil.copy(src, d)
    env = {**os.environ, "POKE_RECORDS": d}
    ps = [subprocess.Popen([sys.executable, __file__, "--child", str(k)], env=env, cwd=SENT) for k in range(2)]
    [p.wait() for p in ps]
    raw = open(os.path.join(d, "r-9155ba1300.json")).read()
    try:
        r = json.loads(raw); have = [x for x in r["sentences"] if x.startswith("경합")]
        print(f"record parses; {len(have)}/{2 * 8 * 25} added wordings survived (lost {2 * 8 * 25 - len(have)})")
    except ValueError as e:
        print("RECORD CORRUPTED:", e)
    leftovers = [f for f in os.listdir(d) if f.endswith(".tmp")]
    print("tmp files left:", leftovers); shutil.rmtree(d)
else:
    sys.path.insert(0, SENT); os.chdir(SENT)
    import record
    from concurrent.futures import ThreadPoolExecutor
    k = sys.argv[-1]; errs = []
    def one(i):
        try: record.heard({"id": "r-9155ba1300", "sentences": []}, f"경합 {k}-{i} 온도")
        except Exception as e: errs.append(f"{type(e).__name__}: {e}")
    with ThreadPoolExecutor(8) as ex: list(ex.map(one, range(200)))
    print(f"child {k}: exceptions {len(errs)} {sorted(set(errs))[:3]}")
