"""Shared start of every 실기기팀 test (decision 7): the owner's hub memory is never touched.

Import this FIRST (before say / record): POKE_RECORDS → a fresh temporary directory, seeded with a read-only
copy of the owner's records so remembered sentences need no model call; whatever the test remembers or
forgets stays in the copy. The owner's directory is checked unchanged at exit."""
import os, sys, shutil, tempfile, hashlib, atexit
HERE = os.path.dirname(os.path.abspath(__file__))
SENT = os.path.dirname(os.path.dirname(HERE))
OWNER = os.path.join(SENT, "build", "records")

def _digest(d):
    h = hashlib.sha1()
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        p = os.path.join(d, f)
        if os.path.isfile(p):
            h.update(f.encode()); h.update(open(p, "rb").read())
    return h.hexdigest()

if not os.environ.get("POKE_RECORDS_HW_ISOLATED"):
    _tmp = tempfile.mkdtemp(prefix="hw-records-")
    if os.path.isdir(OWNER):
        for f in os.listdir(OWNER):
            p = os.path.join(OWNER, f)
            if os.path.isfile(p) and f.endswith(".json"):
                shutil.copy2(p, os.path.join(_tmp, f))
    _before = _digest(OWNER)
    os.environ["POKE_RECORDS"] = _tmp
    os.environ["POKE_RECORDS_HW_ISOLATED"] = "1"
    def _check():
        if _digest(OWNER) != _before:
            print("⚠ the owner's records changed while this test ran (another process?)", file=sys.stderr)
        shutil.rmtree(_tmp, ignore_errors=True)
    atexit.register(_check)
sys.path.insert(0, SENT)

def reseed():
    """Back to a fresh copy of the owner's records (cases stay independent: one case's remembered
    answers must not decide the next case's)."""
    d = os.environ["POKE_RECORDS"]
    for f in os.listdir(d):
        p = os.path.join(d, f)
        if os.path.isfile(p): os.remove(p)
    if os.path.isdir(OWNER):
        for f in os.listdir(OWNER):
            p = os.path.join(OWNER, f)
            if os.path.isfile(p) and f.endswith(".json"):
                shutil.copy2(p, os.path.join(d, f))

MODEL_DOWN = ("CLIError", "/nonexistent", "claude -p", "ModelBanned", "Claude Code CLI", "claude`", "session limit",
              "hit your", "Not logged in", "login", "TimeoutExpired", "Claude Code", "rate limit")

def model_unavailable(blob):
    """A failure that is the model's absence (blocked, usage limit, login, timeout) — SKIP under --no-model, not FAIL."""
    s = blob if isinstance(blob, str) else __import__("json").dumps(blob, ensure_ascii=False, default=str)
    return any(x in s for x in MODEL_DOWN)
