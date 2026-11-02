"""Build garbage collection: the hub keeps sentences (records), not images.

Every op is rebuilt from its record when it is said (0.1 s), so a built image is only a by-product.
This removes build/img files older than max_age_h, except images an armed or stored watch pins
(build/watches/*.json "img"), and 24 h-old lock files nobody holds. say.build() calls maybe() at most
once an hour; `python buildgc.py [--hours N] [--dry]` runs it by hand.
"""
import os, sys, json, time, glob, fcntl

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
STAMP = os.path.join(BUILD, ".gc-stamp")

def pinned():
    keep = set()
    wdir = os.environ.get("POKE_WATCHES", os.path.join(BUILD, "watches"))
    for f in glob.glob(os.path.join(wdir, "*.json")):
        try:
            w = json.load(open(f))
        except (OSError, ValueError):
            continue
        for v in (w.values() if isinstance(w, dict) else []):
            if isinstance(v, dict) and v.get("img"):
                keep.add(os.path.realpath(v["img"])[:-4])
        if isinstance(w, dict) and w.get("img"):
            keep.add(os.path.realpath(w["img"])[:-4])
    return keep

def run(max_age_h=6.0, dry=False):
    now, cut = time.time(), time.time() - max_age_h * 3600
    keep = pinned()
    gone, size = 0, 0
    for f in glob.glob(os.path.join(BUILD, "img", "*")):
        if os.path.isdir(f):
            continue                                       # .build-* temp dirs belong to running builds
        stem = os.path.realpath(f).split(".")[0]
        try:
            st = os.stat(f)
        except OSError:
            continue
        if st.st_mtime > cut or any(stem.startswith(k) for k in keep):
            continue
        size += st.st_size; gone += 1
        if not dry:
            try: os.remove(f)
            except OSError: pass
    locks = 0
    for f in glob.glob(os.path.join(BUILD, "locks", "*.lock")):
        try:
            if os.stat(f).st_mtime > now - 86400:
                continue
            fd = os.open(f, os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # nobody holds it
                if not dry: os.remove(f)
                locks += 1
            except OSError:
                pass
            finally:
                os.close(fd)
        except OSError:
            pass
    return {"files": gone, "bytes": size, "locks": locks, "kept_pinned": len(keep)}

def maybe(every_s=3600):
    """Cheap: at most once per every_s across processes."""
    try:
        if time.time() - os.stat(STAMP).st_mtime < every_s:
            return None
    except OSError:
        pass
    try:
        os.makedirs(BUILD, exist_ok=True)
        open(STAMP, "w").close()
        return run()
    except Exception:
        return None

if __name__ == "__main__":
    h = float(sys.argv[sys.argv.index("--hours") + 1]) if "--hours" in sys.argv else 6.0
    r = run(h, dry="--dry" in sys.argv)
    print(f"{'would remove' if '--dry' in sys.argv else 'removed'} {r['files']} files ({r['bytes'] // 1024} KB), "
          f"{r['locks']} idle lock files; kept images pinned by {r['kept_pinned']} watches")
