"""시험팀 공용: say.py를 수정하지 않고 가져다 쓴다. 빌드 태그는 모두 tt- 로 시작."""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import isolate  # noqa: E402,F401 — 소유자 기록 보호(결정 7): POKE_RECORDS를 임시 사본으로
import os, sys, json, time, statistics
HERE = os.path.dirname(os.path.abspath(__file__))
SENT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, SENT)
os.chdir(SENT)
import say  # noqa: E402

RES = os.path.join(HERE, "results")
os.makedirs(RES, exist_ok=True)
H = '#include "rt.h"\n'

def build_tt(src, tag):
    assert tag.startswith("tt-")
    img, obj, path = say.build(src, tag)
    return img, obj, path, say.check(obj)

def summary(xs):
    xs = [x for x in xs if x is not None]
    if not xs: return {}
    return {"n": len(xs), "min": min(xs), "median": statistics.median(xs), "max": max(xs),
            "p90": sorted(xs)[int(0.9 * (len(xs) - 1))]}

def save(name, obj):
    json.dump(obj, open(os.path.join(RES, name + ".json"), "w"), indent=1, ensure_ascii=False, default=str)

def qemu_leftovers():
    import subprocess
    r = subprocess.run(["pgrep", "-f", "qemu-system-aarch64"], capture_output=True, text=True)
    return [p for p in r.stdout.split() if p]
