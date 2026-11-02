#!/usr/bin/env python3
"""regress — the one command every team runs at the end of a step (시험팀 소유).

    ../sim/body/.venv/bin/python regress.py --quick           # ~2–3 min, no model calls (cached ops only)
    ../sim/body/.venv/bin/python regress.py --full            # + model calls for uncached sentences, netboot, fence prototype
    ../sim/body/.venv/bin/python regress.py --only fault,result
    ../sim/body/.venv/bin/python regress.py --list            # cases, suites, xfail reasons (--by-cat: grouped)
    ../sim/body/.venv/bin/python regress.py --list --cats     # the categories (TESTS.md) and how many cases each has
    ../sim/body/.venv/bin/python regress.py --cat T2,T3       # only what changes time/role and body

Each case is judged against what the product SHOULD do (bench ground truth, not timings). A known
defect is marked xfail with its owner team; when it starts passing it shows as XPASS (remove the mark).
Exit code 0 = no FAIL. Every run is saved to build/regress/<time>-<mode>.json; the summary names the
cases that are newly broken compared with the previous run.

Twin boots run in parallel (say.boot writes one serial file per boot). Cases that patch say's globals
(bench wiring, boot/make_op counters, stdin) run afterwards, one at a time.
"""
import os, sys, io, re, json, time, glob, socket, builtins, contextlib, subprocess, threading, traceback, datetime
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

# The owner's memory (build/records) is never written by regress: every run works on its own copy,
# build/regress/records, refreshed one way from the owner's records (new or newer ones). Subprocesses
# (CLI, demo server, other teams' tests) inherit it through POKE_RECORDS.
MAIN_RECORDS = os.path.join(HERE, "build", "records")
if not os.environ.get("POKE_RECORDS"):
    import shutil
    REG_RECORDS = os.path.join(HERE, "build", "regress", "records")
    os.makedirs(REG_RECORDS, exist_ok=True)
    for f in os.listdir(MAIN_RECORDS) if os.path.isdir(MAIN_RECORDS) else []:
        src, dst = os.path.join(MAIN_RECORDS, f), os.path.join(REG_RECORDS, f)
        if f.endswith(".json") and (not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst)):
            shutil.copy2(src, dst)
    os.environ["POKE_RECORDS"] = REG_RECORDS
import say  # noqa: E402
import claude_cli  # noqa: E402  (say put sim/body on the path)

ARGS = sys.argv[1:]
MODE = "full" if "--full" in ARGS else "quick"
ONLY = set(ARGS[ARGS.index("--only") + 1].split(",")) if "--only" in ARGS else None
PID = os.getpid()
OUT = os.path.join(say.BUILD, "regress")
H = '#include "rt.h"\n'
KST = datetime.timezone(datetime.timedelta(hours=9))

# ── model gate: --quick never calls the model ─────────────────────────────
MODEL_CALLS = {"n": 0, "force_ban": False}
_ask, _agent = claude_cli.ask, claude_cli.agent
class ModelBanned(RuntimeError):
    pass
def _gate(real):
    def f(*a, **k):
        MODEL_CALLS["n"] += 1
        if MODE == "quick" or MODEL_CALLS["force_ban"]:
            raise ModelBanned("--quick: no model calls (this sentence is not cached)")
        return real(*a, **k)
    return f
claude_cli.ask, claude_cli.agent = _gate(_ask), _gate(_agent)

class Skip(Exception):
    pass

# ── cases ────────────────────────────────────────────────────────────────
CASES = []
def case(cid, suite, xfail=None, serial=False, mode="quick"):
    """xfail = (owner team, reason). mode 'full' = only with --full."""
    def deco(fn):
        CASES.append({"id": cid, "suite": suite, "fn": fn, "xfail": xfail, "serial": serial, "mode": mode})
        return fn
    return deco

# ── categories (TESTS.md): what must never change (I) and what is changing under the test (T) ──────────
CATEGORIES = {
    "I1": "불변 · 격리·안전 — op가 노드·방을 해치지 못함",
    "I2": "불변 · 휘발 — 끝나면 노드에 아무것도 남지 않음",
    "I3": "불변 · 보고 — 결과·메시지 형식, 허브가 결과를 믿을 수 있음",
    "I4": "불변 · 허브 견고성 — 동시성·누수·기록 경합",
    "I5": "불변 · 사람 경계 — 확인·거절·되물음·정책",
    "I6": "불변 · 부품 진실 — 드라이버 조각이 실험대 진실과 일치",
    "T1": "변환 · 목적 — 같은 몸, 다른 문장·계획",
    "T2": "변환 · 시간·역할 — 같은 몸, 시간에 따라 역할 교대 (낮 검사 / 밤 관측, 예약, 상시 의도)",
    "T3": "변환 · 몸 — 부품이 옮겨지고·빠지고·새로 붙고·학습됨",
    "T4": "변환 · 기체·경로 — 같은 문장, 다른 아키텍처·부팅 경로",
    "T5": "변환 · 세계 — 노드 밖 환경이 바뀜 (가상 집 물리)",
}
# first matching prefix wins (case id, then suite)
CATEGORY_RULES = [
    ("policy/refused", "I1"), ("policy/", "I2"),
    ("teams/hw/t_power", "I2"),
    ("teams/verify/t_r3", "I1"), ("teams/verify/t_v6", "I1"), ("teams/verify/t_v7", "I1"),
    ("teams/sign/", "I1"),
    ("teams/verify/t_x2", "I5"), ("teams/verify/t_r2", "T3"), ("teams/verify/t_x3", "T3"),
    ("teams/hw/", "T3"), ("teams/x86/", "T4"),
    ("teams/registry/t_registry", "T1"), ("teams/registry/t_world", "T5"), ("teams/role/", "T2"),
    ("hub/parse", "I3"),
    ("fault", "I1"), ("fence", "I1"), ("boottest", "I1"), ("check-fp", "I1"), ("checktest", "I1"),
    ("result", "I3"), ("message", "I3"), ("time", "I3"),
    ("hub", "I4"), ("keylock", "I4"),
    ("confirm", "I5"), ("prefilter", "I5"),
    ("selftest", "I6"), ("frag", "I6"),
    ("sentence", "T1"), ("effects", "T1"), ("record", "T1"), ("demo", "T1"), ("fresh", "T1"),
    ("schedule", "T2"), ("watch", "T2"),
    ("absent", "T3"), ("catalog", "T3"), ("trace", "T3"),
    ("netboot", "T4"),
    ("guard", "I4"),
]
def category(c):
    for pre, cat in CATEGORY_RULES:
        if c["id"].startswith(pre) or c.get("suite", "").startswith(pre):
            return cat
    return "?"
CATS = set(ARGS[ARGS.index("--cat") + 1].upper().split(",")) if "--cat" in ARGS else None

def tag(cid):
    return f"tt-rg{PID}-" + re.sub(r"[^A-Za-z0-9_.-]", "_", cid)

def build_boot(cid, src, bench=None, budget=10, script=()):
    _, obj, path = say.build(src, tag(cid))
    b = say.boot(path, say.default_bench() if bench is None else bench, budget_s=budget, script=script)
    b["check"] = say.check(obj)
    return b

def faults(b):
    return [l for l in b["lines"] if l.startswith("POKE-FAULT")]

def brief(b):
    r = json.dumps(b.get("result"), ensure_ascii=False)
    return (f"off={b['powered_off']} result={r[:140]}{'…' if len(r) > 140 else ''}"
            + (f" result_error={b['result_error']}" if b.get("result_error") else "")
            + (f" warning" if b.get("warning") else "") + (f" fault={faults(b)[0][:70]}" if faults(b) else ""))

def ok_off_err(b, n_fault_lines_max=3):
    r = b.get("result") or {}
    return b["powered_off"] and bool(r.get("error")) and not b.get("result_error") and len(faults(b)) <= n_fault_lines_max

# ---- selftest: each fragment against the bench (selftest.CHECKS) --------
import selftest  # noqa: E402
for _name, _src, _bench, _good in selftest.CHECKS:
    def _f(name=_name, src=_src, bench=_bench, good=_good):
        b = build_boot("selftest-" + name, src, bench)
        r = b["result"] or {}
        return bool(b["powered_off"] and not r.get("error") and good(r, b)), brief(b)
    case(f"selftest/{_name}", "selftest")(_f)

# ---- checktest: the image check refuses / passes (checktest.CASES) ------
import checktest  # noqa: E402
for _i, (_name, _src, _want) in enumerate(checktest.CASES):
    def _f(i=_i, src=_src, want=_want):
        try:
            _, obj, _ = say.build(src, tag(f"check-{i}"))
            c = say.check(obj)
            got = "refuse" if not c["ok"] else "warn" if c.get("warnings") else "pass"
            why = "; ".join((c["problems"] or c.get("warnings") or [])[:1])
        except RuntimeError as e:
            got, why = "refuse", "did not build: " + str(e).splitlines()[0][:100]
        return got == want or (want == "ok" and got != "refuse"), f"{got} (expected {want}) {why[:110]}"
    case(f"checktest/{_i}", "checktest")(_f)

# ---- check false positives: ordinary negative int constants (t_check_fp) -
FP = [
    ("idx-minus1", '#include "led.h"\nvoid op_main(void){ int found = -1; unsigned p; if (led_inputs(0x60, &p)) { fail("x"); return; } for (int i = 0; i < 16; i++) if (!((p >> i) & 1)) { found = i; break; } report_int("first_on", found); }', None),
    ("clamp-minus40", '#include "tmp105.h"\nstatic int clamp(int v){ if (v < -40) return -40; if (v > 125) return 125; return v; }\nvoid op_main(void){ long t; if (tmp105_read_mC(0x48, &t)) { fail("x"); return; } report_int("t", clamp((int)(t / 1000))); }', None),
    ("mask-ffffffff", 'void op_main(void){ volatile unsigned m = 0xFFFFFFFFu; report_int("m", (long)(m & 0xff)); }', None),
    ("neg-table", 'static const int off[4] = { -1, 0, 1, -2 };\nvoid op_main(void){ long s = 0; for (int i = 0; i < 4; i++) s += off[ms() & 3]; report_int("s", s); }', None),
    ("long-minus40", 'void op_main(void){ volatile long v = -40; report_int("v", v); }', None),
]
for _n, _src, _xf in FP:
    def _f(n=_n, src=_src):
        _, obj, _ = say.build(H + src, tag("fp-" + n))
        c = say.check(obj)
        return c["ok"], "; ".join(c["problems"][:1])[:140] or "passed"
    case(f"check-fp/{_n}", "check-fp", xfail=_xf)(_f)

# ---- fault paths: ordinary op bugs (t_fault) — node reports and powers off itself
V1 = ("검사기", "v1 런타임(MMU+EL0+타이머) 본선 통합 전")
FAULT = [
    ("brk", 'void op_main(void){ say("before"); report_int("a", 1); __builtin_trap(); report_int("b", 2); }', 8,
     lambda b: ok_off_err(b) and (b["result"] or {}).get("a") == 1, None),
    ("udf", 'void op_main(void){ __asm__ volatile(".inst 0x00000000"); }', 8, ok_off_err, None),
    ("read-above-4g", 'void op_main(void){ volatile unsigned *p = (volatile unsigned *)0x600000000UL; report_int("v", *p); }', 8, ok_off_err, None),
    ("null-read-nonconst", 'void op_main(void){ volatile unsigned *p = (volatile unsigned *)(ms() >> 60); unsigned a = *p; report_int("r0", a); }', 8, ok_off_err, None),
    ("div-by-zero", 'void op_main(void){ volatile long z = 0; long a = 100; report_int("q", a / z); report_int("r", a % z); }', 8,
     lambda b: b["powered_off"] and (b["result"] or {}).get("q") == 0, None),
    ("recursion-overflow", 'static long f(volatile long n){ volatile char pad[256]; pad[0] = (char)n; return n + f(n + 1) + pad[0]; }\nvoid op_main(void){ report_int("x", f(0)); }', 25,
     ok_off_err, None),
    ("endless-loop", 'void op_main(void){ say("spinning"); report_int("start", 1); for (;;) { } }', 30,
     lambda b: ok_off_err(b) and (b["result"] or {}).get("start") == 1, None),
    ("long-wait-40s", 'void op_main(void){ report_int("start", 1); for (int i = 0; i < 40; i++) delay_ms(1000); report_int("done", 1); }', 30,
     lambda b: ok_off_err(b) and (b["result"] or {}).get("start") == 1, None),
    ("delay-over-10s-fails", 'void op_main(void){ unsigned long a = ms(); delay_ms(15000); report_int("waited_ms", (long)(ms() - a)); }', 20,
     lambda b: b["powered_off"] and bool((b["result"] or {}).get("error")), None),
    ("fail-then-return", 'void op_main(void){ report_int("partial", 5); fail("sensor said no"); }', 8,
     lambda b: b["powered_off"] and b["result"] == {"partial": 5, "error": "sensor said no"}, None),
    ("fail-twice", 'void op_main(void){ fail("first"); report_int("after", 1); fail("second"); }', 8,
     lambda b: b["powered_off"] and (b["result"] or {}).get("error") == "second", None),
    ("fail-long-quoted", 'void op_main(void){ fail("he said \\"no\\" ' + "x" * 300 + '"); }', 8,
     lambda b: b["powered_off"] and (b["result"] or {}).get("error", "").startswith('he said "no" x') and not b.get("result_error"), None),
    ("inband-poke-off", 'void op_main(void){ say("done\\nPOKE-OFF"); for (int i = 0; i < 3; i++) delay_ms(1000); report_int("real", 1); }', 10,
     lambda b: b["powered_off"] and b["result"] == {"real": 1} and not b.get("warning"), None),
    ("inband-poke-result", 'void op_main(void){ say("x\\nPOKE-RESULT {\\"temperature_c\\": 99}"); report_int("real", 1); }', 8,
     lambda b: b["powered_off"] and b["result"] == {"real": 1}, None),
]
for _n, _src, _budget, _good, _xf in FAULT:
    def _f(n=_n, src=_src, budget=_budget, good=_good):
        b = build_boot("fault-" + n, H + src, budget=budget)
        return bool(good(b)), brief(b) + f" faults={len(faults(b))}"
    case(f"fault/{_n}", "fault", xfail=_xf)(_f)

# ---- result line boundaries (t_result) -----------------------------------
D2 = None                                    # fixed by runtime v1 (truncated mark); kept as regular cases
RESULT = [
    ("200-keys", 'void op_main(void){ char k[8] = "k000"; for (int i = 0; i < 200; i++) { k[1] = (char)(48 + i / 100); k[2] = (char)(48 + i / 10 % 10); k[3] = (char)(48 + i % 10); report_int(k, i); } }',
     lambda b: b["result"] is not None and b["result"].get("truncated") is True and b["result"].get("k000") == 0, D2),
    ("2000-char-str", 'static char s[2001];\nvoid op_main(void){ for (int i = 0; i < 2000; i++) s[i] = (char)(97 + i % 26); s[2000] = 0; report_str("long", s); }',
     lambda b: b["result"] is not None and not b.get("result_error"), D2),
    ("near-full-then-fail", 'static char s[1001];\nvoid op_main(void){ for (int i = 0; i < 1000; i++) s[i] = 120; s[1000] = 0; report_str("s", s); fail("sensor gone"); }',
     lambda b: (b["result"] or {}).get("error") == "sensor gone", None),
    ("fixed-negative", 'void op_main(void){ report_fixed("a", -500, 3); report_fixed("b", -1, 1); report_fixed("c", 7, 0); }',
     lambda b: b["result"] == {"a": -0.5, "b": -0.1, "c": 7}, None),
    ("fixed-long-min", 'void op_main(void){ report_fixed("m", (-9223372036854775807L - 1), 3); }',
     lambda b: b["result"] is not None and abs(b["result"]["m"] + 9223372036854775.808) < 1, None),
    ("fixed-decimals-minus1", 'void op_main(void){ report_fixed("d", 5, -1); }',
     lambda b: b["result"] is not None and not b.get("result_error"), None),
    ("int-long-min", 'void op_main(void){ report_int("m", (-9223372036854775807L - 1)); }', lambda b: b["result"] == {"m": -9223372036854775808}, None),
    ("utf8-str", 'void op_main(void){ report_str("msg", "온도 정상"); }', lambda b: b["result"] == {"msg": "온도 정상"}, None),
    ("empty", 'void op_main(void){ }', lambda b: b["result"] == {} and b["powered_off"], None),
]
for _n, _src, _good, _xf in RESULT:
    def _f(n=_n, src=_src, good=_good):
        b = build_boot("result-" + n, H + src)
        return bool(b["powered_off"] and good(b)), brief(b)
    case(f"result/{_n}", "result", xfail=_xf)(_f)

# ---- fence: an op reaching past its windows (existing cases from t_result) --
FENCE = [
    ("computed-uart", 'void op_main(void){ volatile unsigned long a = 0xFE2; unsigned long b = a << 20; b |= 0x1000; *(volatile unsigned *)b = 90; *(volatile unsigned *)b = 10; report_int("ok", 1); }',
     lambda b: ok_off_err(b) and "Z" not in [l.strip() for l in b["lines"]]),
    ("computed-pm-reset", 'void op_main(void){ volatile unsigned long a = 0xFE1; unsigned long b = a << 20; *(volatile unsigned *)(b + 0x24) = 0x5a000000u | 10; *(volatile unsigned *)(b + 0x1c) = 0x5a000020u; for (int i = 0; i < 100; i++) delay_ms(10); report_int("still_here", 1); }',
     ok_off_err),
]
for _n, _src, _good in FENCE:
    def _f(n=_n, src=_src, good=_good):
        b = build_boot("fence-" + n, H + src)
        return bool(good(b)), brief(b)
    case(f"fence/{_n}", "fence")(_f)

# ---- boottest: the verify team's fence cases, built through say.build (teams/verify/edge/boottest.py) --
_BT = {"imgs": None, "lock": threading.Lock()}
def _boottest_mod():
    sys.path.insert(0, os.path.join(HERE, "teams", "verify", "edge"))
    import boottest
    return boottest
def _bt_imgs():
    with _BT["lock"]:
        if _BT["imgs"] is None:
            _BT["imgs"] = _boottest_mod().build_mainline()
        return _BT["imgs"]
try:
    _bt_cases = _boottest_mod().CASES
except Exception:
    _bt_cases = []
for _n, _ok in _bt_cases:
    def _f(n=_n, ok=_ok):
        b = say.boot(_bt_imgs()[n], say.default_bench(), budget_s=30)
        f = next((l for l in b["lines"] if l.startswith("POKE-FAULT")), "")
        return bool(b["powered_off"] and ok(b, f)), brief(b)
    case(f"boottest/{_n}", "boottest")(_f)

# ---- hub robustness (D5 leak, D2 hub side) ---------------------------------
def _no_leak():
    _, _, path = say.build(H + 'void op_main(void){ report_int("x", 1); }', tag("hub-leak"))
    try:
        say.boot(path, {"t1 temperature": 128000}, budget_s=5); exc = None
    except Exception as e:
        exc = type(e).__name__
    time.sleep(0.3)
    left = subprocess.run(["pgrep", "-f", os.path.basename(path)], capture_output=True, text=True).stdout.split()
    return not left, f"bench refused ({exc}); QEMU processes left: {len(left)}"
case("hub/no-qemu-left-after-bad-bench", "hub")(_no_leak)
def _parse():
    cases = [['POKE-RESULT {"a":1,"b'], ['POKE-RESULT [1]'], ['POKE-RESULT {"a":1}', 'POKE-RESULT {"a":2}'], []]
    got = [say.parse_result(c) for c in cases]
    ok = got[0][0] is None and got[0][1] and got[1][0] is None and got[2][0] == {"a": 2} and got[3] == (None, None)
    return bool(ok), str(got)[:150]
case("hub/parse-result-never-raises", "hub")(_parse)

def _concurrent():
    """Two people (or two browser tabs on the threaded demo server) say the same sentence at once."""
    get_op("온도 알려줘")
    def one(_):
        try:
            op = say.make_op("온도 알려줘", log=lambda *a: None)
            b = say.boot(op["path"], say.default_bench(), budget_s=8)
            return "ok" if b["powered_off"] and (b["result"] or {}).get("temp_c") else f"bad result {json.dumps(b['result'], ensure_ascii=False)[:60]}"
        except Exception as e:
            return f"{type(e).__name__}: {' '.join(str(e).split())[:80]}"
    with ThreadPoolExecutor(4) as ex:
        rs = list(ex.map(one, range(12)))
    bad = sorted(set(r for r in rs if r != "ok"))
    return not bad, f"{rs.count('ok')}/12 ok" + ("; " + " | ".join(bad)[:200] if bad else "")
case("hub/same-sentence-4-at-once", "hub", serial=True)(_concurrent)

def _no_serial_left():
    _, _, path = say.build(H + 'void op_main(void){ }', tag("hub-serial"))
    before = set(glob.glob(os.path.join(say.BUILD, "serial", f"{PID}-*")))
    try: say.boot(path, {"t1 temperature": 128000}, budget_s=3)
    except Exception: pass
    left = set(glob.glob(os.path.join(say.BUILD, "serial", f"{PID}-*"))) - before
    for f in left:
        try: os.remove(f)
        except OSError: pass
    return not left, f"{len(left)} serial file(s) left in build/serial after a boot whose bench was refused"
case("hub/no-serial-file-after-bad-bench", "hub", serial=True)(_no_serial_left)

def _record_race():
    r = subprocess.run([PY, os.path.join(HERE, "teams", "test", "t_record_save_race.py")], capture_output=True, text=True, cwd=HERE, timeout=300,
                       env={k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"})
    out = r.stdout + r.stderr
    ok = "lost 0)" in out and "exceptions 0 " in out and out.count("exceptions 0 ") == 2 and "CORRUPTED" not in out
    return ok, " | ".join(l.strip()[:90] for l in out.strip().splitlines()[-4:])
case("hub/record-concurrent-saves", "hub", serial=True)(_record_race)

# ---- effects: what the node really did on the bus (POKE-EFFECTS) vs the manifest, and the wipe after it -----
EFFECTS = [("온도 알려줘", {}, "read"), ("지금 몇 시야?", {}, "read"), ("자기장 세기 알려줘", {}, "read"),
           ("LED 3번 켜줘", {}, "led_set"), ("LED 1번이랑 2번 켜줘", {}, "led_set"),
           ("온도가 25도 넘으면 LED 0번을 켜고, 아니면 꺼줘", {"t1 temperature": 27250}, "led_set")]
for _s, _bench, _want in EFFECTS:
    def _f(s=_s, bench=_bench, want=_want):
        op = get_op(s)
        b = say.boot(op["path"], {**say.default_bench(), **bench}, budget_s=20)
        rep = say.effects_report(b, op["path"])
        acts = sorted({a["fn"] for a in rep.get("actions", [])})
        wc = b.get("wipe_check") or {}
        ok = (b["powered_off"] and rep.get("ok") is True and not rep.get("undeclared")
              and (acts == [] if want == "read" else want in acts) and wc.get("nonzero") == 0 and wc.get("bytes", 0) > 0)
        return bool(ok), f"effects ok={rep.get('ok')} actions={acts} undeclared={rep.get('undeclared')} transfers={rep.get('transfers')} wipe={wc.get('nonzero')}/{wc.get('bytes')}"
    case(f"effects/{_s[:22]}", "effects")(_f)

# ---- other teams' own tests, run as they are (exit code 0 = pass) ---------------------------------------
TEAM_TESTS = [("verify/t_r2", ["teams/verify/t_r2.py"], "quick"), ("verify/t_r3", ["teams/verify/t_r3.py"], "quick"),
              ("verify/t_x2", ["teams/verify/t_x2.py"], "quick"), ("verify/t_x3", ["teams/verify/t_x3.py"], "quick"),
              ("hw/t_newparts", ["teams/hw/t_newparts.py", "--no-model"], "quick"), ("hw/t_power", ["teams/hw/t_power.py"], "quick"),
              ("verify/t_v6", ["teams/verify/t_v6.py"], "quick"), ("verify/t_v7", ["teams/verify/t_v7.py"], "quick"),
              ("hw/t_body_change", ["teams/hw/t_body_change.py", "--no-model", "--via-hub"], "quick"), ("hw/t_learn", ["teams/hw/t_learn.py"], "quick"),
              ("x86/selftest", ["x86.py", "selftest"], "full"),
              ("registry/t_registry", ["teams/registry/t_registry.py"], "quick"),
              ("registry/t_world", ["teams/registry/t_world.py"], "quick"),
              ("role/t_role_shift", ["teams/role/t_role_shift.py"], "quick"),
              ("sign/t_signed_boot", ["teams/sign/t_signed_boot.py"], "quick")]   # only hub-signed images boot (netboot, Pi format)   # L0–L3 registry, capability-API images   # x86 PC over PXE: fences, wipe, ~50 s
for _n, _argv, _mode in TEAM_TESTS:
    def _f(argv=_argv):
        if not os.path.exists(os.path.join(HERE, argv[0])):
            raise Skip(f"{argv[0]} not found")
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        if MODE == "quick": env["POKE_CLAUDE"] = "/nonexistent/claude-blocked-by-regress-quick"
        r = subprocess.run([PY, os.path.join(HERE, argv[0]), *argv[1:]], capture_output=True, text=True, cwd=os.path.dirname(os.path.join(HERE, argv[0])),
                           env=env, timeout=600, stdin=subprocess.DEVNULL)
        tail = [l for l in (r.stdout + r.stderr).strip().splitlines() if l.strip()][-1:] or [""]
        return r.returncode == 0, f"exit {r.returncode}: {tail[0][:140]}"
    case(f"teams/{_n}", "teams", serial=True, mode=_mode,
         )(_f)

# ---- fragment edge values (t_frag_edge) ----------------------------------
TMP_SRC = H + '#include "tmp105.h"\nvoid op_main(void){ long t; int e = tmp105_read_mC(0x48, &t); if (e) { report_int("e", e); fail("tmp105"); return; } report_int("mC", t); }'
def _tmp(points, tol):
    bad = []
    for mC in points:
        b = build_boot(f"tmp-{mC}", TMP_SRC, {"t1 temperature": mC}, budget=6)
        got, truth = (b["result"] or {}).get("mC"), b["truth"].get("temperature_mC")
        if got is None or truth is None or abs(got - truth) > tol: bad.append(f"{truth}→{got}")
    return not bad, ("all within %d mC" % tol) if not bad else "off: " + ", ".join(bad)
case("frag/tmp105-sign-range", "frag")(lambda: _tmp([-55000, -500, 0, 25000], 0))
case("frag/tmp105-resolution", "frag")(lambda: _tmp([-10250, -62, 62, 25250, 124937], 63))

# ---- sentences: cached ops (quick) / generated (full), judged by the bench --
def _nums(r):
    return [v for k, v in (r or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
def _hours(r):
    return {v for k, v in (r or {}).items() if "hour" in k and isinstance(v, int)} | \
           {int(m.group(1)) for v in (r or {}).values() if isinstance(v, str) for m in [re.match(r"(\d{1,2}):\d{2}", v)] if m}
SENTENCES = [
    ("온도 알려줘", {"t1 temperature": 23750}, lambda r, t, b: any(abs(v - 23.75) <= 0.5 for v in _nums(r))),
    ("지금 몇 시야?", {}, lambda r, t, b: bool(_hours(r) & {datetime.datetime.now(datetime.timezone.utc).hour, datetime.datetime.now(KST).hour})),
    ("LED 3번 켜줘", {}, lambda r, t, b: t["leds_on"] == [3]),
    ("온도가 25도 넘으면 LED 0번을 켜고, 아니면 꺼줘", {"t1 temperature": 23750}, lambda r, t, b: t["leds_on"] == []),
    ("온도가 25도 넘으면 LED 0번을 켜고, 아니면 꺼줘", {"t1 temperature": 27250}, lambda r, t, b: t["leds_on"] == [0]),
    ("자기장 세기 알려줘", {"m1 mag-x": 27273, "m1 mag-y": 0}, lambda r, t, b: any(abs(v - 300) <= 10 for v in _nums(r))),
    ("LED 0번부터 3번까지 0.2초 간격으로 차례로 켜줘", {}, lambda r, t, b: t["leds_on"] == [0, 1, 2, 3]),
    ("Turn on LED 7 and tell me the temperature in Fahrenheit", {"t1 temperature": 25000}, lambda r, t, b: t["leds_on"] == [7] and any(abs(v - 77.0) <= 1.0 for v in _nums(r))),
    ("나침반 방향 알려줘", {"m1 mag-x": 9091, "m1 mag-y": 9091}, lambda r, t, b: any(abs(v - 45) <= 3 or abs(v - 315) <= 3 for v in _nums(r))),
    ("temperature가 30도 이상이면 LED 1 blink 시켜, 아니면 LED 1 꺼줘", {"t1 temperature": 31000}, lambda r, t, b: "error" not in (r or {}) and t["leds_on"] == []),
    ("LED 2번 켜고 12초 뒤에 꺼줘", {}, lambda r, t, b: t["leds_on"] == [] and (b["node_ms"] or 0) >= 11500),
    ("LED 20번 켜줘", None, "range"),            # product decision: out-of-range numbers are answered on the hub, no boot
]
OPS = {}                                    # sentence → make_op result (filled serially before the parallel phase)
def get_op(sentence):
    if sentence not in OPS:
        n0 = MODEL_CALLS["n"]
        try:
            OPS[sentence] = say.make_op(sentence, log=lambda *a: None)
        except Exception as e:
            OPS[sentence] = e if MODEL_CALLS["n"] == n0 or MODE != "quick" else Skip("not cached (needs --full)")
    v = OPS[sentence]
    if isinstance(v, Exception):
        raise v
    return v
def op_src(op):
    return op.get("src") or open(op["path"].rsplit(".", 1)[0] + ".c").read()
for _i, (_s, _bench, _good) in enumerate(SENTENCES):
    def _f(i=_i, s=_s, bench=_bench, good=_good):
        if isinstance(good, str):                  # the hub must refuse this sentence itself (Refusal kind), no boot
            try:
                op = get_op(s)
            except Skip:
                raise
            except Exception as e:
                kind = getattr(e, "kind", None)
                return kind == good, f"{type(e).__name__} kind={kind}: {str(e)[:100]}"
            return False, "an op was made (expected the hub to refuse: %s)" % good
        op = get_op(s)
        b = build_boot(f"sent-{i}", op_src(op), bench, budget=30)
        return bool(b["powered_off"] and good(b["result"], b["truth"], b)), brief(b) + f" leds={b['truth'].get('leds_on')}"
    case(f"sentence/{_i}:{_s[:24]}", "sentence")(_f)
SENT_PREFETCH = [s for s, _, _ in SENTENCES]

# ---- absent devices (t_absent) — patches say.BENCH, so serial -----------
ABSENT = [("온도 알려줘", "tmp105", None), ("온도 알려줘", "tmp105", 0x4a), ("LED 3번 켜줘", "pca9552", None),
          ("온도가 25도 넘으면 LED 0번을 켜고, 아니면 꺼줘", "pca9552", None), ("지금 몇 시야?", "ds1338", None), ("자기장 세기 알려줘", "*", None)]
for _i, (_s, _model, _moved) in enumerate(ABSENT):
    def _f(i=_i, s=_s, model=_model, moved=_moved):
        op = get_op(s)
        full = list(say.BENCH)
        say.BENCH = [] if model == "*" else ([(m, q, moved if m == model else a) for m, q, a in full] if moved else [x for x in full if x[0] != model])
        try:
            b = build_boot(f"absent-{i}", op_src(op), {"t1 temperature": 27250} if model == "pca9552" else {}, budget=10)
        finally:
            say.BENCH = full
        return bool(ok_off_err(b, 0)), brief(b)
    case(f"absent/{_i}:{_model}{'@%#x' % _moved if _moved else ''}", "absent", serial=True)(_f)

# ---- policy (decisions 1, 2, 4) -------------------------------------------
def _counting(fn, box):
    def f(*a, **k):
        box["n"] += 1
        return fn(*a, **k)
    return f
@contextlib.contextmanager
def patched(stdin_text="", make_op=None):
    """Count boots (every module holding say.boot), optionally replace make_op, feed stdin, swallow stdout."""
    boots = {"n": 0}
    real_boot, real_make = say.boot, say.make_op
    mods = [m for m in list(sys.modules.values()) if m is not None and getattr(m, "boot", None) is real_boot]
    mmods = [m for m in list(sys.modules.values()) if m is not None and getattr(m, "make_op", None) is real_make]
    for m in mods: m.boot = _counting(real_boot, boots)
    if make_op:
        for m in mmods: m.make_op = make_op
    old_in, old_argv = sys.stdin, sys.argv
    sys.stdin = io.StringIO(stdin_text); sys.argv = ["say.py"]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            yield boots, buf
    finally:
        for m in mods: m.boot = real_boot
        for m in mmods: m.make_op = real_make
        sys.stdin, sys.argv = old_in, old_argv

def _refused_op(cached):
    """The first existing checktest case that still builds and that the check refuses."""
    for i, (name, src, should) in enumerate(checktest.CASES):
        if should != "refuse": continue
        try: img, obj, path = say.build(src, tag(f"refused-{i}"))
        except RuntimeError: continue
        c = say.check(obj)
        if not c["ok"]: break
    else:
        raise Skip("no checktest case is both buildable and refused any more")
    REFUSED_SRC = src
    return lambda *a, **k: {"src": REFUSED_SRC, "img": img, "obj": obj, "path": path, "key": "tt", "cached": cached, "gen_s": 0, "rounds": 0, "check": c}
for _cached in (False, True):
    def _f(cached=_cached):
        mk = _refused_op(cached)
        with patched("y\n", make_op=mk) as (boots, buf):
            try: say.run("시험: 거부된 이미지", bench={}); exc = None
            except SystemExit as e: exc = f"exit {e.code}"
            except Exception as e: exc = repr(e)[:80]
        return boots["n"] == 0, f"boots={boots['n']} exc={exc}"
    case(f"policy/refused-{'cached' if _cached else 'new'}-never-boots", "policy", serial=True)(_f)

def _v1_default():
    _, _, path = say.build(H + 'void op_main(void){ report_int("x", 1); }', tag("v1probe"))
    syms = say.sh(["aarch64-elf-nm", path[:-4] + ".elf"])
    ok = "enter_el0" in syms and "sentence_svc" in syms
    return ok, "v1 runtime linked (enter_el0, sentence_svc)" if ok else "the image has no v1 runtime symbols"
case("policy/v1-runtime-is-default", "policy")(_v1_default)

def _qmp_peek(img, ranges, budget=8):
    """Boot like say.boot, wait for the node's own reset, then read RAM. → {start: bytes}"""
    port = 40000 + int.from_bytes(os.urandom(2), "little") % 20000
    serial = os.path.join(say.BUILD, "serial", f"{PID}-peek-{port}.txt"); os.makedirs(os.path.dirname(serial), exist_ok=True)
    devs = sum((["-device", f"{m},id={q},bus=i2c-bus.1,address={a:#x}"] for m, q, a in say.BENCH), [])
    q = subprocess.Popen(["qemu-system-aarch64", "-M", "raspi4b", "-kernel", img, "-display", "none", "-S", "-serial", f"file:{serial}",
                          "-qmp", f"tcp:127.0.0.1:{port},server=on,wait=off", "-monitor", "none", "-action", "reboot=shutdown,shutdown=pause", *devs],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        mon = say.QMP(port); mon.call("cont"); t0 = time.time()
        while time.time() - t0 < budget and mon.call("query-status").get("running"):
            time.sleep(0.05)
        out = {}
        for start, n in ranges:
            txt = mon.call("human-monitor-command", **{"command-line": f"xp /{n // 4}wx {start:#x}"})
            out[start] = [int(w, 16) for w in re.findall(r"0x([0-9a-f]{8})", txt)]
        try: mon.call("quit")
        except Exception: pass
        return out
    finally:
        try: q.wait(5)
        except subprocess.TimeoutExpired: q.kill()
        if os.path.exists(serial): os.remove(serial)
def _syms(path):
    return {l.split()[-1]: int(l.split()[0], 16) for l in say.sh(["aarch64-elf-nm", path[:-4] + ".elf"]).splitlines() if len(l.split()) == 3}
def _words_to_bytes(ws):
    return b"".join(w.to_bytes(4, "little") for w in ws)
def _op_region_zero():
    src = H + 'static volatile char keep[64] = "secret-op-data";\nvoid op_main(void){ volatile char buf[128]; for (int i = 0; i < 128; i++) buf[i] = (char)(65 + i % 26); report_int("k", keep[0] + buf[5]); }'
    _, _, path = say.build(src, tag("zeroregion"))
    sy = _syms(path)
    lo, hi = sy["__rt_text_end"], sy.get("__rt_stack_base", sy["__op_rw_end"])
    mem = _qmp_peek(path, [(lo, hi - lo)])
    raw = _words_to_bytes(mem[lo])
    nz = sum(1 for b in raw if b)
    return len(raw) == hi - lo and nz == 0, f"{nz} non-zero bytes in the op's pages + runtime data [{lo:#x}, {hi:#x}) ({len(raw)} B read) after the node's reset"
case("policy/op-region-zero-after-reset", "policy")(_op_region_zero)
def _no_op_text_anywhere():
    marker = "QZXV-op-secret-7341"
    src = H + 'void op_main(void){ say("%s"); report_str("m", "%s"); }' % (marker, marker)
    _, _, path = say.build(src, tag("residue"))
    sy = _syms(path)
    lo, hi = 0x80000, sy["__rt_end"]
    raw = _words_to_bytes(_qmp_peek(path, [(lo, hi - lo)])[lo])
    hits = [hex(lo + m.start()) for m in re.finditer(re.escape(marker[:12].encode()), raw)]
    return not hits, (f"the op's text survives the reset at {', '.join(hits[:4])} (runtime stack/result buffer: [{sy.get('__rt_stack_base', 0):#x}, {sy['__rt_stack_top']:#x}))"
                      if hits else f"no trace of the op's text in [{lo:#x}, {hi:#x})")
case("policy/no-op-text-anywhere-after-reset", "policy")(_no_op_text_anywhere)
def _zero_fill():
    src = H + 'static volatile char keep[64] = "secret-op-data";\nvoid op_main(void){ report_int("k", keep[0]); }'
    _, _, path = say.build(src, tag("zerofill"))
    syms = {l.split()[-1]: int(l.split()[0], 16) for l in say.sh(["aarch64-elf-nm", path[:-4] + ".elf"]).splitlines() if len(l.split()) == 3}
    ranges = [(syms["op_main"], 32), (syms["keep"], 32)]
    mem = _qmp_peek(path, ranges)
    words = [w for v in mem.values() for w in v]
    nz = sum(1 for w in words if w)
    return bool(words) and nz == 0, f"{nz}/{len(words)} non-zero words left at op_main/keep after the node's reset"
case("policy/op-area-zeroed-before-reset", "policy")(_zero_fill)

# ---- record: normalised / parameter cache (decision 5) — no model calls allowed
def _no_model(sentence, base, bench, good):
    get_op(base)                                   # the base sentence must be cached already
    n0 = MODEL_CALLS["n"]
    MODEL_CALLS["force_ban"] = True                # even with --full: a variant must never be generated (and cached) here
    try:
        op = say.make_op(sentence, log=lambda *a: None)
    except Exception as e:
        return False, f"model needed ({type(e).__name__}) — not served from the record of {base!r}"
    finally:
        MODEL_CALLS["force_ban"] = False
    if MODEL_CALLS["n"] != n0:
        return False, "the model was called"
    b = build_boot("rec-" + sentence, op_src(op), bench)
    return bool(good(b)), "served without the model; " + brief(b) + f" leds={b['truth'].get('leds_on')}"
REC = [("온도 알려줘.", "온도 알려줘", {"t1 temperature": 23750}, lambda b: any(abs(v - 23.75) <= 0.5 for v in _nums(b["result"]))),
       ("온도  알려 줘", "온도 알려줘", {"t1 temperature": 23750}, lambda b: any(abs(v - 23.75) <= 0.5 for v in _nums(b["result"]))),
       ("LED 5번 켜줘", "LED 3번 켜줘", {}, lambda b: b["truth"]["leds_on"] == [5]),
       ("LED 4번 켜 줘", "LED 3번 켜줘", {}, lambda b: b["truth"]["leds_on"] == [4])]
for _s, _base, _bench, _good in REC:
    def _f(s=_s, base=_base, bench=_bench, good=_good):
        return _no_model(s, base, bench, good)
    case(f"record/{_s}", "record", serial=True)(_f)

def _record_fields():
    try:
        import record
    except ImportError:
        return False, "no record.py"
    get_op("온도 알려줘")
    recs = [p for p in glob.glob(os.path.join(say.BUILD, "**", "*.json"), recursive=True) if "record" in p]
    for p in recs:
        try: d = json.load(open(p))
        except Exception: continue
        blob = json.dumps(d, ensure_ascii=False)
        if "온도 알려줘" in blob:
            need = ["model", "date", "sentence"]
            miss = [k for k in need if k not in blob]
            return not miss, f"{os.path.relpath(p, HERE)} missing {miss}" if miss else f"{os.path.relpath(p, HERE)} has sentence/model/date"
    return False, "no record file mentions 온도 알려줘"
case("record/fields", "record", serial=True)(_record_fields)

def _kst():
    op = get_op("지금 몇 시야?")
    b = build_boot("kst", op_src(op), {}, budget=10)
    r = b["result"] or {}
    kst = datetime.datetime.now(KST).hour
    return (kst in _hours(r) and "KST" in json.dumps(r)), f"KST hour {kst}; " + brief(b)
case("time/kst", "time")(_kst)

# ---- confirm / NEED (decision 6) through say.run --------------------------
def _run(sentence, stdin_text, bench=None):
    with patched(stdin_text) as (boots, buf):
        try: r = say.run(sentence, bench=bench or {}); exc = None
        except SystemExit as e: r, exc = None, f"exit {e.code}"
        except Skip: raise
        except ModelBanned: raise Skip("not cached (needs --full)")
        except Exception as e: r, exc = None, repr(e)[:100]
    if exc and "ModelBanned" in exc: raise Skip("not cached (needs --full)")
    return r, boots["n"], exc, buf.getvalue()
def _c_read():
    get_op("온도 알려줘")
    r, n, exc, out = _run("온도 알려줘", "")
    return n == 1 and exc is None, f"boots={n} exc={exc}"
def _c_write_no():
    get_op("LED 3번 켜줘")
    r, n, exc, out = _run("LED 3번 켜줘", "n\n")
    return n == 0, f"answered 'n': boots={n} exc={exc}"
def _c_write_yes():
    get_op("LED 3번 켜줘")
    r, n, exc, out = _run("LED 3번 켜줘", "y\n")
    leds = (((r or {}).get("boot") or {}).get("truth") or {}).get("leds_on")
    return n == 1 and leds == [3], f"answered 'y': boots={n} leds={leds} exc={exc}"
def _c_ambiguous():
    r, n, exc, out = _run("불 좀 켜줘", "y\n")
    return n == 0 and "?" in out, f"boots={n} (expected a question, no boot) exc={exc}"
def _c_rtc_set():
    r, n, exc, out = _run("시계를 2020년 1월 1일 0시로 맞춰줘", "y\n")
    return n == 0, f"boots={n} (rtc_set is denied by policy) exc={exc}"
case("confirm/read-runs-without-asking", "confirm", serial=True)(_c_read)
case("confirm/write-declined-no-boot", "confirm", serial=True)(_c_write_no)
case("confirm/write-approved-boots", "confirm", serial=True)(_c_write_yes)
case("confirm/ambiguous-write-asks", "confirm", serial=True)(_c_ambiguous)
case("confirm/rtc-set-denied", "confirm", serial=True, mode="full")(_c_rtc_set)
def _always():
    r1, n1, e1, _ = _run("LED 11번 깜빡여줘", "a\n")
    r2, n2, e2, _ = _run("LED 11번 깜빡여줘", "")
    return n1 == 1 and n2 == 1, f"'a' → boots={n1}; then no answer → boots={n2} (approval kept in the record) {e1 or ''} {e2 or ''}"
case("confirm/always-approval-remembered", "confirm", serial=True, mode="full")(_always)
CORPUS = [  # (sentence, should boot when the person answers "n" to any question)
    ("지금 날짜 알려줘", True), ("LED 몇 번이 켜져 있어?", True), ("자기장 x축 값만 알려줘", True), ("온도를 화씨로만 알려줘", True),
    ("LED 9번 꺼줘", False), ("LED 1번이랑 2번 켜줘", False),               # writes: a confirm line, "n" → no boot
    ("그거 꺼줘", False), ("불 좀 꺼줘", False),                              # ambiguous writes: a question, no boot
    ("시계를 2020년 1월 1일로 맞춰줘", False),                                # denied by policy
    ("사진 찍어줘", False),                                                   # nothing on this node can do it
]
def _corpus():
    right, notes = 0, []
    for s, should_boot in CORPUS:
        try:
            r, n, exc, out = _run(s, "n\n")
        except Skip:
            notes.append(f"{s}: not cached"); continue
        ok = (n == 1) if should_boot else (n == 0)
        right += ok
        if not ok: notes.append(f"{s}: boots={n}")
    return right >= 9, f"{right}/{len(CORPUS)} as expected; " + "; ".join(notes)[:200]
case("confirm/corpus-10", "confirm", serial=True, mode="full")(_corpus)

# ---- people's messages: failures without a traceback (CLI) ------------------
PY = sys.executable
def _cli(sentence, env_extra=None, path=None):
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env.update(env_extra or {})
    if path: env["PATH"] = path
    r = subprocess.run([PY, os.path.join(HERE, "say.py"), sentence], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120, cwd=HERE)
    return r.returncode, r.stdout + r.stderr
def _m_no_qemu():
    get_op("온도 알려줘")
    d = os.path.join(OUT, f"nopath-{PID}"); os.makedirs(d, exist_ok=True)
    for t in ("aarch64-elf-gcc", "aarch64-elf-ld", "aarch64-elf-objcopy", "aarch64-elf-objdump", "aarch64-elf-nm", "cc"):
        src = subprocess.run(["which", t], capture_output=True, text=True).stdout.strip()
        if src and not os.path.exists(os.path.join(d, t)): os.symlink(src, os.path.join(d, t))
    code, out = _cli("온도 알려줘", path=d + ":/usr/bin:/bin")
    ko = bool(re.search(r"[가-힣]", out.split("온도 알려줘")[-1]))
    return "Traceback" not in out and ko, f"exit {code}, traceback={'Traceback' in out}, korean={ko}: {out.strip().splitlines()[-1][:90] if out.strip() else ''}"
def _m_no_claude():
    code, out = _cli(f"회귀 시험용 새 문장 {PID} 온도", env_extra={"POKE_CLAUDE": "/nonexistent/claude"})
    ko = bool(re.search(r"[가-힣]", out.split("온도")[-1]))
    return "Traceback" not in out and ko, f"exit {code}, traceback={'Traceback' in out}, korean={ko}: {out.strip().splitlines()[-1][:90] if out.strip() else ''}"
def _cli_exit(sentence, flags, want):
    def f():
        get_op("LED 3번 켜줘"); get_op("온도 알려줘")       # cached bases, so the CLI never needs the model here
        r = subprocess.run([PY, os.path.join(HERE, "say.py"), *flags, sentence], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=120, cwd=HERE, env={k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"})
        out = r.stdout + r.stderr
        last = [l for l in out.strip().splitlines() if l.strip()][-1:] or [""]
        return r.returncode == want and "Traceback" not in out, f"exit {r.returncode} (want {want}): {last[0][:100]}"
    return f
case("message/cli-range-exit3", "message", serial=True)(_cli_exit("LED 20번 켜줘", [], 3))
case("message/cli-write-no-exit3", "message", serial=True)(_cli_exit("LED 3번 켜줘", ["--no"], 3))
case("message/cli-write-yes-exit0", "message", serial=True)(_cli_exit("LED 3번 켜줘", ["--yes"], 0))
case("message/cli-read-exit0", "message", serial=True)(_cli_exit("온도 알려줘", [], 0))
case("message/no-qemu", "message", serial=True)(_m_no_qemu)
case("message/no-claude", "message", serial=True)(_m_no_claude)

# ---- full only: one sentence the hub has never heard, so the model path runs every --full ---------
FRESH = ["온도를 켈빈으로 알려줘", "자기장 y축 값만 알려줘", "LED 12번 상태 알려줘", "지금 몇 월 며칠이야?", "온도를 화씨와 섭씨로 둘 다 알려줘",
         "자기장 z축 값 알려줘", "LED 15번이 켜져 있는지 알려줘", "오늘 무슨 요일이야?"]
def _fresh():
    import record
    heard = {x for r in record.all_records() for x in r.get("sentences", [])}
    todo = [x for x in FRESH if x not in heard]
    if not todo:
        raise Skip("every fresh sentence is already in the record — add more to FRESH")
    s = todo[0]; n0 = MODEL_CALLS["n"]
    r, n, exc, out = _run(s, "y\n")
    b = (r or {}).get("boot") or {}
    res = b.get("result") or {}
    gen = MODEL_CALLS["n"] - n0
    return gen >= 1 and n == 1 and b.get("powered_off") and not res.get("error"), f"{s!r}: model calls {gen}, boots={n}, result={json.dumps(res, ensure_ascii=False)[:100]} {exc or ''}"
case("fresh/one-new-sentence", "fresh", serial=True, mode="full")(_fresh)

# ---- 4th wave: refusals before any op (D1), one generation per sentence (D2), standing-watch caps (D3), body catalog (H7)
def _cli_fast(sentence, want_exit=3):
    t0 = time.time()
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env["POKE_CLAUDE"] = "/nonexistent/claude-blocked-by-regress"          # a model call would fail loudly
    r = subprocess.run([PY, os.path.join(HERE, "say.py"), sentence], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60, cwd=HERE, env=env)
    out = r.stdout + r.stderr; s_ = time.time() - t0
    booted = "[노드] 전원 켬" in out or "부팅 (트윈)" in out
    last = [l for l in out.strip().splitlines() if l.strip()][-1:] or [""]
    return r.returncode == want_exit and not booted and "Claude Code를 설치" not in out and "Traceback" not in out, \
           f"exit {r.returncode}, booted={booted}, {s_:.2f} s: {last[0][:100]}"
PRE = [("시계를 오후 3시로 바꿔줘", "deny"), ("LED -1번 꺼줘", "range"), ("LED 2.5번 켜줘", "range"), ("LED 16번 켜줘", "range"),
       ("온도가 200도 넘으면 LED 1번 켜줘", "range")]
for _s, _k in PRE:
    def _f(s=_s, k=_k):
        import intent
        pf = intent.prefilter(s)
        ok_cli, d = _cli_fast(s)
        return bool(pf and pf[0] == k and ok_cli), f"prefilter={pf[0] if pf else None} (want {k}); CLI {d}"
    case(f"prefilter/{_s}", "prefilter")(_f)
def _no_false_prefilter():
    import intent
    corpus = [s for s, _, _ in SENTENCES] + [c[0] for c in CORPUS] + ["온도가 -10도보다 낮으면 LED 2번 켜줘", "LED 0번부터 15번까지 짝수만 켜줘",
              "온도가 25.5도 이상이면 LED 1번 켜줘", "지금 몇 시야?", "시계 몇 시야", "오늘 날짜 알려줘"]
    should = {"시계를 2020년 1월 1일로 맞춰줘", "LED 20번 켜줘"}
    wrong = [s for s in corpus if (intent.prefilter(s) is not None) != (s in should)]
    return not wrong, f"{len(corpus)} sentences, wrong pre-refusals/misses: {wrong[:4]}"
case("prefilter/no-false-refusal", "prefilter")(_no_false_prefilter)

def _keylock_once():
    """Four threads say one sentence the hub has never heard → one model call; the others are served the first's record."""
    import record, tempfile
    old = record.RECORDS
    record.RECORDS = tempfile.mkdtemp(prefix="keylock-", dir=OUT)
    os.environ["POKE_LEGACY_OPS"] = "0"
    try:
        n0 = MODEL_CALLS["n"]
        def one(_):
            try:
                op = say.make_op("LED 14번을 켜 주세요", log=lambda *a: None); return "cached" if op.get("cached") else "made"
            except Exception as e:
                return type(e).__name__
        with ThreadPoolExecutor(4) as ex:
            rs = list(ex.map(one, range(4)))
        calls = MODEL_CALLS["n"] - n0
        return calls == 1 and rs.count("made") == 1 and rs.count("cached") == 3, f"model calls {calls}, outcomes {rs}"
    finally:
        record.RECORDS = old; os.environ.pop("POKE_LEGACY_OPS", None)
case("keylock/same-new-sentence-4-at-once", "keylock", serial=True, mode="full")(_keylock_once)

def _watch_caps():
    W = say.WATCH
    caps_ok = W.get("min_period_s", 0) >= 60 and W.get("fault_stop") == 2 and W.get("max_active", 99) <= 4 and W.get("ttl_h", 999) <= 24 and W.get("max_s", 99) <= 5
    rd, wr = op_src(get_op("온도 알려줘")), op_src(get_op("LED 3번 켜줘"))
    c_read, c_write = say.watch_check(rd), say.watch_check(wr)
    # verdicts on real boots: a read → ok; a write → stop at once; a fault twice → strike then stop
    _, _, p_r = say.build(rd, tag("watch-read")); b_r = say.boot(p_r, say.default_bench(), budget_s=10)
    _, _, p_w = say.build(wr, tag("watch-write")); b_w = say.boot(p_w, say.default_bench(), budget_s=10)
    _, _, p_f = say.build(H + 'void op_main(void){ __builtin_trap(); }', tag("watch-fault")); b_f = say.boot(p_f, say.default_bench(), budget_s=10)
    v_r = say.watch_verdict(b_r, p_r, 0)[0]; v_w = say.watch_verdict(b_w, p_w, 0)[0]
    f1 = say.watch_verdict(b_f, p_f, 0); f2 = say.watch_verdict(b_f, p_f, f1[2])
    ok = caps_ok and c_read.get("ok") and not c_write.get("ok") and v_r == "ok" and v_w == "stop" and f1[0] == "strike" and f2[0] == "stop"
    return bool(ok), (f"caps {'ok' if caps_ok else W}; watch_check read={c_read.get('ok')} write={c_write.get('ok')}; "
                      f"verdict read={v_r} write={v_w} fault→{f1[0]}→{f2[0]}")
case("watch/caps-and-verdicts", "watch")(_watch_caps)

def _catalog_body():
    names = sorted(say.catalog().keys())
    return names == ["led", "mag", "rtc", "tmp105"], f"catalog on the base bench: {names}"
case("catalog/base-bench-is-what-is-there", "catalog")(_catalog_body)

# ---- trace check (decision 6, product F1): a re-pronounced op is held against the card's traces --------
_E12 = {}
def _e12():
    if not _E12:
        sys.path.insert(0, os.path.join(HERE, "teams", "test"))
        import importlib, io as _io
        m = importlib.import_module("e12_trace")
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            pass
        rows = []
        for sentence, cands in m.CASES.items():
            op = say.make_op(sentence, log=lambda *a: None)
            b0 = say.boot(op["path"], say.default_bench(), budget_s=10)
            ref = [{"result": b0["result"], "fx": m.TC.fx_summary(b0, op["path"]), "body": "B", "bench": None, "ok": True}]
            for i, (name, src, want, kind) in enumerate(cands):
                b, path = m.run(src, tag(f"e12-{abs(hash(sentence)) % 1000}-{i}"))
                v = m.TC.compare({}, b.get("result"), m.TC.fx_summary(b, path), body_fp="B", bench=None, ref=ref)
                rows.append((sentence, name, kind, v["verdict"]))
        _E12["rows"] = rows
    return _E12["rows"]
def _trace_same():
    rows = [r for r in _e12() if r[2] == "same"]
    bad = [f"{r[0]}/{r[1]}→{r[3]}" for r in rows if r[3] not in ("match", "partial")]
    return not bad, f"{len(rows) - len(bad)}/{len(rows)} same-work re-pronunciations adopted {bad}"
def _trace_wrong():
    rows = [r for r in _e12() if r[2] == "wrong"]
    bad = [f"{r[0]}/{r[1]}→{r[3]}" for r in rows if r[3] != "mismatch"]
    return not bad, f"{len(rows) - len(bad)}/{len(rows)} different-work ops refused; adopted: {bad}"
case("trace/same-work-adopted", "trace", serial=True)(_trace_same)
case("trace/different-work-refused", "trace", serial=True)(_trace_wrong)   # fixed 08:49 (제품: 레지스터별 마지막 쓴 값·읽은 부품·값으로 대조)

# ---- full only: scheduled boots, netboot, verify prototype fence ----------
def _schedule():
    r, n, exc, out = _run("30초 동안 10초마다 온도 알려줘", "y\n", {"t1 temperature": 23750})
    return n >= 3, f"boots={n} exc={exc}"
case("schedule/30s-every-10s", "schedule", serial=True, mode="full")(_schedule)

def _netboot():
    port = 17000 + PID % 1000
    nb = os.path.join(HERE, "netboot")
    lock = open(os.path.join(say.BUILD, "regress-netboot.lock"), "w")
    import fcntl; fcntl.flock(lock, fcntl.LOCK_EX)           # netboot/root and vboot-out are shared: one run at a time
    log = open(os.path.join(OUT, f"netboot-{PID}.log"), "w")
    t = subprocess.Popen([PY, "-u", os.path.join(nb, "tftpd.py"), "--port", str(port)], stdout=log, stderr=subprocess.STDOUT)
    import seal                                              # the netboot path is signed boot: the hub's key, the Pi's format
    v = subprocess.Popen([PY, "-u", "vboot.py", "--port", str(port), "--signed", seal.ensure_key()[1]], cwd=nb, stdout=log, stderr=subprocess.STDOUT)
    try:
        time.sleep(1.5); good = []
        for s, check_ in (("온도 알려줘", lambda r: any(abs(x - 23.75) <= 0.5 for x in _nums(r))), ("LED 3번 켜줘", lambda r: not (r or {}).get("error")), ("온도 알려줘", lambda r: bool(r))):
            _, _, path = say.build(op_src(get_op(s)), tag("nb-" + s))
            b = say.boot_netboot_twin(path, budget_s=60)
            good.append(bool(b.get("powered_off")) and check_(b.get("result")))
        return all(good), f"{sum(good)}/{len(good)} via TFTP"
    finally:
        v.terminate(); t.terminate(); time.sleep(0.3); v.kill(); t.kill(); log.close(); lock.close()
case("netboot/three-in-a-row", "netboot", serial=True, mode="full")(_netboot)

# ---- demo: the owner's morning list (teams/product/MORNING.md) through the demo screen's HTTP API (T7) ----
import atexit, urllib.request, urllib.parse
UI = {"proc": None, "port": None}
def _ui():
    if UI["proc"] is None:
        UI["port"] = 18765 + PID % 1000
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        env["POKE_UI_PORT"] = str(UI["port"])
        if MODE == "quick": env["POKE_CLAUDE"] = "/nonexistent/claude-blocked-by-regress-quick"
        UI["log"] = open(os.path.join(OUT, f"ui-{PID}.log"), "w")
        UI["proc"] = subprocess.Popen([PY, os.path.join(HERE, "ui", "server.py")], env=env, cwd=HERE, stdout=UI["log"], stderr=subprocess.STDOUT)
        atexit.register(lambda: (UI["proc"].kill(), UI["proc"].wait()))
        for _ in range(100):
            try: urllib.request.urlopen(f"http://127.0.0.1:{UI['port']}/twin", timeout=1); break
            except OSError: time.sleep(0.1)
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{UI['port']}/reset", data=b"{}", method="POST"), timeout=5)
    return UI["port"]
def _say_ui(sentence, answers=(), temp_c=None):
    """One sentence through /say (SSE). Answers asks in order. → list of events."""
    port = _ui(); answers = list(answers)
    q = {"sentence": sentence, **({"temp_c": str(temp_c)} if temp_c is not None else {})}
    evs, rid = [], None
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/say?" + urllib.parse.urlencode(q), timeout=200) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "): continue
            ev = json.loads(line[6:]); evs.append(ev)
            if ev.get("stage") == "run": rid = ev["run"]
            if ev.get("stage") == "ask":
                a = answers.pop(0) if answers else "n"
                urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/answer", data=json.dumps({"id": rid, "text": a}).encode(),
                                                              headers={"Content-Type": "application/json"}, method="POST"), timeout=5)
            if ev.get("stage") == "end": break
    stop = next((e for e in evs if e.get("stage") == "stop"), None)
    if stop and stop.get("kind") == "error" and "Claude" in (stop.get("text", "") + stop.get("hint", "")) and MODE == "quick":
        raise Skip("needs the model (not in the record yet) — run --full")
    return evs
def _ev(evs, stage, **kw):
    return [e for e in evs if e.get("stage") == stage and all(e.get(k) == v for k, v in kw.items())]
def _res(evs):
    r = _ev(evs, "result"); return r[-1] if r else {}
def _desc(evs):
    st = next((e for e in evs if e.get("stage") == "stop"), None)
    r = _res(evs)
    return (f"boots={len(_ev(evs, 'boot', state='on'))} asks={[e.get('kind') for e in _ev(evs, 'ask')]} "
            + (f"stop={st.get('kind')}: {st.get('text', '')[:60]} " if st else "")
            + f"result={json.dumps(r.get('result'), ensure_ascii=False)[:90]} leds={(r.get('truth') or {}).get('leds_on')}"
            + (f" empty={_ev(evs, 'empty')[-1].get('text', '')[:40]!r}" if _ev(evs, "empty") else ""))
def _empty_ok(evs):
    e = _ev(evs, "empty"); return bool(e) and e[-1].get("powered_off") and e[-1].get("zeroed")
DEMO = [
    ("1a 온도 알려줘 23.75", lambda: _say_ui("온도 알려줘", temp_c=23.75),
     lambda ev: not _ev(ev, "ask") and any(abs(v - 23.5) <= 0.5 for v in _nums(_res(ev).get("result"))) and _empty_ok(ev)),
    ("1b 온도 알려줘 27", lambda: _say_ui("온도 알려줘", temp_c=27),
     lambda ev: any(abs(v - 27.0) <= 0.5 for v in _nums(_res(ev).get("result")))),
    ("2 지금 몇 시야", lambda: _say_ui("지금 몇 시야?"),
     lambda ev: datetime.datetime.now(KST).hour in _hours(_res(ev).get("result"))),
    ("3 LED 3번 켜줘", lambda: _say_ui("LED 3번 켜줘", ["y"]),
     lambda ev: [e.get("kind") for e in _ev(ev, "ask")] == ["confirm"] and 3 in (_res(ev).get("truth") or {}).get("leds_on", [])),
    ("4 LED 7번 켜 줘 (기록, 모델 0)", lambda: _say_ui("LED 7번 켜 줘", ["y"]),
     lambda ev: any(e.get("cached") for e in _ev(ev, "op", state="ready")) and {3, 7} <= set((_res(ev).get("truth") or {}).get("leds_on", []))),
    ("5a 25도 조건 23.75 → LED0 꺼짐", lambda: _say_ui("온도가 25도 넘으면 LED 0번을 켜고, 아니면 꺼줘", ["y"], 23.75),
     lambda ev: 0 not in (_res(ev).get("truth") or {}).get("leds_on", [0])),
    ("5b 25도 조건 27 → LED0 켜짐", lambda: _say_ui("온도가 25도 넘으면 LED 0번을 켜고, 아니면 꺼줘", ["y"], 27),
     lambda ev: 0 in (_res(ev).get("truth") or {}).get("leds_on", [])),
    ("6 LED 0~5번 0.5초 간격 (기록)", lambda: _say_ui("LED 0번부터 5번까지 0.5초 간격으로 차례로 켜줘", ["y"]),
     lambda ev: {0, 1, 2, 3, 4, 5} <= set((_res(ev).get("truth") or {}).get("leds_on", []))),
    ("7 불 좀 켜줘 → 되물음 → 2번", lambda: _say_ui("불 좀 켜줘", ["2번", "y"]),
     lambda ev: _ev(ev, "ask") and _ev(ev, "ask")[0].get("kind") == "need" and 2 in (_res(ev).get("truth") or {}).get("leds_on", [])),
    ("8 LED 20번 → 범위 밖, 부팅 0", lambda: _say_ui("LED 20번 켜줘"),
     lambda ev: not _ev(ev, "boot", state="on") and any(e.get("kind") == "range" for e in _ev(ev, "stop"))),
    ("9 사진 찍어줘 → 불가, 부팅 0", lambda: _say_ui("사진 찍어줘"),
     lambda ev: not _ev(ev, "boot", state="on") and any(e.get("kind") == "cannot" for e in _ev(ev, "stop"))),
    ("덤 시계 맞추기 → 정책 거부", lambda: _say_ui("시계를 2020년 1월 1일 0시로 맞춰줘", ["y"]),
     lambda ev: not _ev(ev, "boot", state="on") and any(e.get("kind") == "deny" for e in _ev(ev, "stop"))),
]
for _n, _go, _good in DEMO:
    def _f(go=_go, good=_good):
        ev = go()
        return bool(good(ev)), _desc(ev)
    case(f"demo/{_n}", "demo", serial=True)(_f)
def _ui_get(port, path):
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10).read())
def _ui_post(port, path, body):
    try:
        return json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                                                         headers={"Content-Type": "application/json"}, method="POST"), timeout=10).read())
    except urllib.error.HTTPError as e:
        return {"http": e.code}
def _card_roundtrip():
    """Card of 'LED 3번 켜줘' exported from the main hub, imported into an empty second hub (own POKE_RECORDS),
    served there with no model, then forgotten there. The main records are only read."""
    import tempfile, shutil, urllib.error
    port = _ui()
    rec = next((r for r in _ui_get(port, "/records") if r["sentence"] == "LED 3번 켜줘"), None)
    if not rec: raise Skip("no record for LED 3번 켜줘")
    card = _ui_get(port, f"/card?id={rec['id']}")
    exp = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/export?id={rec['id']}", timeout=10).read())
    d = tempfile.mkdtemp(prefix="regress-card-", dir=OUT); port2 = port + 1
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env.update({"POKE_UI_PORT": str(port2), "POKE_RECORDS": d, "POKE_CLAUDE": "/nonexistent/claude-blocked", "POKE_LEGACY_OPS": "0"})
    p2 = subprocess.Popen([PY, os.path.join(HERE, "ui", "server.py")], env=env, cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            try: _ui_get(port2, "/twin"); break
            except OSError: time.sleep(0.1)
        imp = _ui_post(port2, "/import", {"card": exp})
        old = UI["port"]; UI["port"] = port2
        try:
            ev = _say_ui("LED 6번 켜줘", ["y"])
            served = 6 in ((_res(ev).get("truth") or {}).get("leds_on") or []) and any(e.get("cached") for e in _ev(ev, "op", state="ready"))
            _ui_post(port2, "/forget", {"id": imp.get("id")})
            after = _ui_get(port2, "/records")
        finally:
            UI["port"] = old
        ok = card.get("fits") is True and bool(card.get("lines")) and imp.get("ok") and served and not after
        return bool(ok), f"card fits={card.get('fits')} lines={len(card.get('lines') or [])}; import={imp}; imported card served 'LED 6번' with no model={served}; records after forget={len(after)}"
    finally:
        p2.kill(); p2.wait(); shutil.rmtree(d, ignore_errors=True)
case("demo/card-export-import-forget", "demo", serial=True)(_card_roundtrip)
def _demo_long():
    ev = _say_ui("30초 동안 10초마다 온도 알려줘", ["y"])
    r = _res(ev).get("result") or {}
    n = max(len(_ev(ev, "boot", state="on")), r.get("boots", 0) if isinstance(r.get("boots"), int) else 0)
    return n >= 4 and not _ev(ev, "stop"), f"scheduled boots={n} " + _desc(ev)
case("demo/10 30초 동안 10초마다 (예약 4회)", "demo", serial=True, mode="full")(_demo_long)

# ── runner ───────────────────────────────────────────────────────────────
def owner_records_digest():
    """The owner's memory (build/records) as {file: sha1} — lock files and temporaries left out."""
    import hashlib
    out = {}
    if os.path.isdir(MAIN_RECORDS):
        for f in sorted(os.listdir(MAIN_RECORDS)):
            p = os.path.join(MAIN_RECORDS, f)
            if f.endswith(".json") and os.path.isfile(p):
                out[f] = hashlib.sha1(open(p, "rb").read()).hexdigest()
    return out

def run_case(c):
    t0 = time.time()
    try:
        ok, detail = c["fn"]()
        status = ("XPASS" if ok else "XFAIL") if c["xfail"] else ("PASS" if ok else "FAIL")
    except Skip as e:
        status, detail = "SKIP", str(e)
    except ModelBanned as e:
        status, detail = "SKIP", str(e)
    except Exception as e:
        tb = traceback.format_exc().strip().splitlines()
        status = "XFAIL" if c["xfail"] else "ERROR"
        detail = f"{type(e).__name__}: {' '.join(str(e).split())[:160]} @ {tb[-3].strip()[:80] if len(tb) > 2 else ''}"
    return {"id": c["id"], "suite": c["suite"], "status": status, "detail": detail, "s": round(time.time() - t0, 1),
            "xfail": list(c["xfail"]) if c["xfail"] else None}

def previous(limit=40):
    """Each case's most recent status across earlier runs (an --only run covers only some cases)."""
    runs = sorted(glob.glob(os.path.join(OUT, "*-*.json")))[-limit:]
    before, src = {}, {}
    for f in reversed(runs):
        try: d = json.load(open(f))
        except Exception: continue
        for r in d.get("results", []):
            if r["id"] not in before:
                before[r["id"]], src[r["id"]] = r["status"], os.path.basename(f)
    return before, src, (os.path.basename(runs[-1]) if runs else None)

def main():
    if "--list" in ARGS:
        if "--cats" in ARGS:
            for k, v in CATEGORIES.items():
                print(f"{k}  {sum(category(c) == k for c in CASES):>3}  {v}")
            return 0
        for c in sorted(CASES, key=category) if "--by-cat" in ARGS else CASES:
            print(f"{category(c):<3} {c['suite']:<10} {c['id']:<52} {c['mode']:<5} {'serial' if c['serial'] else '':<6} {('xfail ' + c['xfail'][0] + ': ' + c['xfail'][1]) if c['xfail'] else ''}")
        return 0
    os.makedirs(OUT, exist_ok=True)
    try:                                       # preflight: the mainline must build, or nothing below means anything
        say.build(H + 'void op_main(void){ report_int("x", 1); }', tag("preflight"))
    except Exception as e:
        print("regress: the mainline does not build right now — probably a team mid-edit; try again in a few minutes.\n  "
              + " ".join(str(e).split())[:300])
        if "--force" not in ARGS:
            return 2
    sel = [c for c in CASES if (MODE == "full" or c["mode"] == "quick") and (not ONLY or c["suite"] in ONLY) and (not CATS or category(c) in CATS)]
    owner_before = owner_records_digest()
    t0 = time.time()
    print(f"regress --{MODE}: {len(sel)} cases" + (f" (only {','.join(sorted(ONLY))})" if ONLY else ""), flush=True)
    for s in SENT_PREFETCH + [a[0] for a in ABSENT] + [r[1] for r in REC]:       # make_op serially (it writes per-key files)
        if not ONLY or ONLY & {"sentence", "absent", "record", "confirm", "time", "netboot", "message"}:
            try: get_op(s)
            except Exception: pass
    results = []
    lock = threading.Lock()
    def one(c):
        r = run_case(c)
        with lock:
            results.append(r)
            mark = {"PASS": "✓", "XFAIL": "·", "XPASS": "★", "SKIP": "-", "FAIL": "✗", "ERROR": "✗"}[r["status"]]
            print(f"{mark} {r['status']:<5} {r['id']:<52} {r['s']:>5}s  {r['detail'][:150]}", flush=True)
    with ThreadPoolExecutor(max_workers=int(os.environ.get("REGRESS_JOBS", "4"))) as ex:
        list(ex.map(one, [c for c in sel if not c["serial"]]))
    for c in [c for c in sel if c["serial"]]:
        one(c)
    for f in glob.glob(os.path.join(say.BUILD, "img", f"tt-rg{PID}-*")):
        try: os.remove(f)
        except OSError: pass
    owner_after = owner_records_digest()
    changed = sorted(f for f in set(owner_before) | set(owner_after) if owner_before.get(f) != owner_after.get(f))
    results.append({"id": "guard/owner-records-unchanged", "suite": "guard", "s": 0, "xfail": None,
                    "status": "FAIL" if changed else "PASS",
                    "detail": (f"the owner's records changed during this run: {', '.join(changed[:6])}"
                               + (" (another process may have written them; regress itself uses build/regress/records)" if changed else ""))
                              if changed else f"{len(owner_after)} owner records byte-identical before and after"})
    print(f"{'✓ PASS ' if not changed else '✗ FAIL '} guard/owner-records-unchanged  {results[-1]['detail'][:150]}", flush=True)
    before, before_src, prev_name = previous()
    order = {c["id"]: i for i, c in enumerate(CASES)}
    results.sort(key=lambda r: order.get(r["id"], 10 ** 6))
    counts = {k: sum(r["status"] == k for r in results) for k in ("PASS", "FAIL", "ERROR", "XFAIL", "XPASS", "SKIP")}
    newly = [r for r in results if r["status"] in ("FAIL", "ERROR") and before.get(r["id"]) not in (None, "FAIL", "ERROR")]
    for r in newly:
        r["was"] = f"{before[r['id']]} in {before_src[r['id']]}"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    doc = {"mode": MODE, "time": stamp, "secs": round(time.time() - t0), "counts": counts, "model_calls": MODEL_CALLS["n"],
           "previous": prev_name, "newly_broken": [r["id"] for r in newly], "results": results}
    json.dump(doc, open(os.path.join(OUT, f"{stamp}-{MODE}.json"), "w"), indent=1, ensure_ascii=False)
    print(f"\nregress --{MODE} {time.strftime('%H:%M')}: " + ", ".join(f"{v} {k.lower()}" for k, v in counts.items() if v)
          + f" — {round(time.time() - t0)} s, model calls {MODEL_CALLS['n']}" + (" (all blocked: --quick)" if MODE == "quick" and MODEL_CALLS["n"] else ""))
    by = {}
    for r in results:
        by.setdefault(category(r), {}).setdefault(r["status"], 0); by[category(r)][r["status"]] += 1
    print("by category: " + " · ".join(f"{k} " + "/".join(f"{v}{st[0].lower()}" for st, v in sorted(by[k].items())) for k in sorted(by)))
    doc["by_category"] = by
    json.dump(doc, open(os.path.join(OUT, f"{stamp}-{MODE}.json"), "w"), indent=1, ensure_ascii=False)
    if prev_name:
        print("compared with each case's last earlier run: " + (f"NEWLY BROKEN {len(newly)}: " + ", ".join(f"{r['id']} (was {r['was']})" for r in newly) if newly else "nothing newly broken"))
    for r in results:
        if r["status"] == "XPASS":
            print(f"★ XPASS {r['id']} — fixed? remove its xfail ({r['xfail'][0]}: {r['xfail'][1]})")
    for r in results:
        if r["status"] in ("FAIL", "ERROR"):
            print(f"✗ {r['id']}: {r['detail'][:200]}\n    repro: ../sim/body/.venv/bin/python regress.py --only {r['suite']}")
    return 1 if counts["FAIL"] or counts["ERROR"] else 0

if __name__ == "__main__":
    sys.exit(main())
