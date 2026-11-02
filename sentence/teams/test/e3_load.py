"""E3 부하·지구력: 기억된 문장들을 병렬 트윈(기본 8)으로 수백 번 — make_op(기록)+build+boot를 스레드마다 통째로.
정확도(실험대 진실), 꺼짐, 누수(QEMU 프로세스·build/serial 파일·build/img 임시 디렉터리·이 프로세스의 fd), 시간 분포.
    cd sentence && ../sim/body/.venv/bin/python teams/test/e3_load.py [N] [JOBS]"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import isolate  # noqa: E402,F401 — 소유자 기록 보호(결정 7): POKE_RECORDS를 임시 사본으로
import os, sys, time, json, glob, subprocess, statistics, threading
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__)); SENT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, SENT); os.chdir(SENT)
import say, claude_cli  # noqa: E402
def banned(*a, **k): raise RuntimeError("E3: no model calls")
claude_cli.ask = banned
N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
JOBS = int(sys.argv[2]) if len(sys.argv) > 2 else 8
def nums(r): return [v for v in (r or {}).values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
MIX = [
    ("온도 알려줘", {"t1 temperature": 23750}, lambda r, t: any(abs(v - 23.75) <= 0.1 for v in nums(r))),
    ("온도 알려줘", {"t1 temperature": -10250}, lambda r, t: any(abs(v + 10.25) <= 0.1 for v in nums(r))),
    ("LED 3번 켜줘", {}, lambda r, t: t["leds_on"] == [3]),
    ("LED 12번 켜줘", {}, lambda r, t: t["leds_on"] == [12]),
    ("온도가 25도 넘으면 LED 0번을 켜고, 아니면 꺼줘", {"t1 temperature": 27250}, lambda r, t: t["leds_on"] == [0]),
    ("온도가 25도 넘으면 LED 0번을 켜고, 아니면 꺼줘", {"t1 temperature": 23750}, lambda r, t: t["leds_on"] == []),
    ("자기장 세기 알려줘", {"m1 mag-x": 27273, "m1 mag-y": 0}, lambda r, t: any(abs(v - 300) <= 10 for v in nums(r))),
    ("지금 몇 시야?", {}, lambda r, t: bool(r) and not r.get("error")),
    ("LED 1번이랑 2번 켜줘", {}, lambda r, t: t["leds_on"] == [1, 2]),
]
def snap():
    q = subprocess.run(["pgrep", "-P", str(os.getpid())], capture_output=True, text=True).stdout.split()   # this process's children only
    return {"qemu": len(q), "serial_files": len(glob.glob(os.path.join(say.BUILD, "serial", f"{os.getpid()}-*"))),
            "img_tmp_dirs": len(glob.glob(os.path.join(say.BUILD, "img", ".build-*"))),
            "fds": len(os.listdir("/dev/fd")), "threads": threading.active_count()}
def one(i):
    s, bench, good = MIX[i % len(MIX)]
    t0 = time.time()
    try:
        op = say.make_op(s, log=lambda *a: None)
        t1 = time.time()
        b = say.boot(op["path"], {**say.default_bench(), **bench}, budget_s=20)
        ok = bool(b["powered_off"]) and good(b["result"], b["truth"])
        return {"i": i, "s": s, "ok": ok, "off": b["powered_off"], "make_s": round(t1 - t0, 3), "boot_to_off_ms": b["boot_to_off_ms"],
                "qemu_total_ms": b["qemu_total_ms"], "total_s": round(time.time() - t0, 3), "result": b["result"], "leds": b["truth"].get("leds_on"),
                "warning": b.get("warning"), "result_error": b.get("result_error")}
    except Exception as e:
        import traceback
        where = " < ".join(f"{os.path.basename(x.filename)}:{x.lineno} {x.name}" for x in reversed(traceback.extract_tb(e.__traceback__)[-4:]))
        return {"i": i, "s": s, "ok": False, "off": False, "error": f"{type(e).__name__}: {' '.join(str(e).split())[:120]} @ {where}", "total_s": round(time.time() - t0, 3)}
def pct(xs, p): xs = sorted(xs); return xs[min(len(xs) - 1, int(p * (len(xs) - 1)))]
def main():
    for s, _, _ in MIX: say.make_op(s, log=lambda *a: None)       # warm: every sentence is in the record
    before = snap(); t0 = time.time()
    with ThreadPoolExecutor(JOBS) as ex:
        rows = list(ex.map(one, range(N)))
    wall = time.time() - t0; time.sleep(1.0); after = snap()
    ok = sum(r["ok"] for r in rows); off = sum(r["off"] for r in rows)
    tot = [r["total_s"] for r in rows]; bto = [r["boot_to_off_ms"] for r in rows if r.get("boot_to_off_ms") is not None]
    mk = [r["make_s"] for r in rows if "make_s" in r]
    summ = {"n": N, "jobs": JOBS, "ok": ok, "powered_off": off, "wall_s": round(wall, 1), "boots_per_s": round(N / wall, 2),
            "total_s": {"min": min(tot), "p50": pct(tot, .5), "p90": pct(tot, .9), "p99": pct(tot, .99), "max": max(tot)},
            "make_op_s": {"p50": pct(mk, .5), "p90": pct(mk, .9), "max": max(mk)} if mk else {},
            "boot_to_off_ms": {"p50": pct(bto, .5), "p90": pct(bto, .9), "max": max(bto)} if bto else {},
            "before": before, "after": after,
            "errors": sorted({r.get("error") for r in rows if r.get("error")})[:6],
            "wrong": [{k: r.get(k) for k in ("s", "result", "leds", "warning", "result_error")} for r in rows if not r["ok"] and not r.get("error")][:6]}
    print(json.dumps(summ, ensure_ascii=False, indent=1))
    json.dump({"summary": summ, "rows": rows}, open(os.path.join(HERE, "results", f"e3-{time.strftime('%Y%m%d-%H%M%S')}-{N}x{JOBS}.json"), "w"), indent=1, ensure_ascii=False)
if __name__ == "__main__":
    main()
