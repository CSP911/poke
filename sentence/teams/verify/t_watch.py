"""5차 감사 1부: 상시 의도(watch.py, hub._arm_watch)가 STANDING-CAPS를 실제로 강제하는가.
Model-free: say.make_op is replaced by fixed ops; watches and records live in temp dirs.
    ../sim/body/.venv/bin/python teams/verify/t_watch.py
A case marked FINDING is a gap the audit found (expected to fail until the product fixes it)."""
import os, sys, json, time, threading, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
os.environ["POKE_RECORDS"] = tempfile.mkdtemp(prefix="verify-records-")
os.environ["POKE_WATCHES"] = tempfile.mkdtemp(prefix="verify-watches-")
import say, watch, hub, record as REC

MAN = '/* MANIFEST {"v": 1, "uses": [{"fn": "%s"%s}], "max_s": %s, "summary_ko": "점검"} */\n'
READ = MAN % ("tmp105_read_mC", "", 2) + '#include "rt.h"\n#include "tmp105.h"\n#define POKE_ADDR_TMP105 0x48\n' \
       'void op_main(void){ long t; if (tmp105_read_mC(POKE_ADDR_TMP105, &t)) { fail("t"); return; } report_int("hit", t > 30000); }'
NOHIT = READ.replace('report_int("hit", t > 30000);', 'report_int("t", t);')
FAULTY = READ.replace('report_int("hit", t > 30000);', '{ volatile long *volatile p = 0; report_int("hit", *p); }')
WRITE = MAN % ("led_set", ', "targets": [0], "end": "leave"', 1) + '#include "rt.h"\n#include "led.h"\nvoid op_main(void){ led_set(0x60, 0, 0); report_int("hit", 1); }'
SLOW = READ.replace('"max_s": 2', '"max_s": 9')

CUR = {"src": READ}
def fake_make_op(sentence, log=None, **kw):
    return say._op_dict(CUR["src"], "vt-watch-" + str(abs(hash(CUR["src"])) % 10**8), cached=True, gen_s=0, rounds=0)
say.make_op = fake_make_op
BOOTS = []
_boot = say.boot
def counting_boot(*a, **k):
    BOOTS.append(a[0]); return _boot(*a, **k)
say.boot = counting_boot

def fresh():
    for w in watch.all_watches():
        try: os.remove(watch._path(w["id"]))
        except OSError: pass
    BOOTS.clear()

def armed(src, spec=None, bench=None):
    CUR["src"] = src
    d, prob = watch.prepare("시험 감시", spec or {"check": "온도 30도 넘었나", "every_s": 60})
    if prob: return None, prob
    w, prob = watch.arm(d)
    return w, prob

good = n = 0
def chk(name, ok, finding=False):
    global good, n
    n += 1; good += bool(ok)
    print(f"{'✓' if ok else '✗'} {'FINDING ' if finding and not ok else ''}{name}")

# 1 무장 전 판정
fresh(); w, prob = armed(WRITE)
chk("쓰기 op는 무장되지 않음 (watch_check)", w is None and "읽기만" in (prob or ""))
fresh(); w, prob = armed(SLOW)
chk("max_s 9 > 5 op는 무장되지 않음", w is None and "5초" in (prob or ""))
# 2 읽기 전용 허용 목록·예산 빌드, 해시 고정
fresh(); w, prob = armed(READ)
side = json.load(open(w["img"][:-4] + ".i2c.json"))
import rtcfg
cfg = rtcfg.read(open(w["img"], "rb").read())
chk("감시 이미지 = 주어진 읽기 전용 허용 목록, 쓰기 함수 0", side["source"] == "given" and all(not d.get("max_writes") and d["write_regs"] in ([], [1]) for d in side["allowlist"]["devices"]))
chk("RCFG 예산 = min(max_s·1000+1000, 5000) = 3000 ms", cfg["budget_ms"] == 3000)
chk("주기 하한: every_s 5 → 60", watch.plan({"check": "x", "every_s": 5})[0]["every_s"] == 60)
# 3 매 부팅 판정, hit → 알림만, 행동 자동 실행 없음
fresh(); w, _ = armed(READ, {"check": "c", "every_s": 60, "then": "LED 0번 켜줘"})
evs = watch.tick(bench={"t1 temperature": 31000}, notify=False, log=lambda *a: None)
kinds = [e["kind"] for e in evs]
chk("hit → 'hit'+'pending_action' 사건, 부팅은 점검 1회뿐(행동 부팅 없음)", "hit" in kinds and "pending_action" in kinds and len(BOOTS) == 1)
chk("hit 뒤 한 번 알리고 끝 (state done)", watch.load(w["id"])["state"] == "done")
# 4 연속 실패 2회 중지
fresh(); w, _ = armed(FAULTY)
watch.tick(notify=False, log=lambda *a: None); s1 = watch.load(w["id"])
watch._now, real_now = (lambda: time.time() + 61), watch._now
watch.tick(notify=False, log=lambda *a: None); s2 = watch.load(w["id"]); watch._now = real_now
chk("FAULT 1회 → strike(계속), 2회 → 멈춤", s1["state"] == "armed" and s1["bad"] == 1 and s2["state"] == "stopped")
# 5 hit 필드 없음 → 멈춤
fresh(); w, _ = armed(NOHIT); watch.tick(notify=False, log=lambda *a: None)
chk("hit을 보고하지 않는 op → 멈춤", watch.load(w["id"])["state"] == "stopped")
# 6 이미지 해시 고정
fresh(); w, _ = armed(READ)
with open(w["img"], "ab") as f: f.write(b"\0")
watch.tick(notify=False, log=lambda *a: None)
chk("무장 뒤 이미지가 바뀌면 부팅 없이 멈춤", watch.load(w["id"])["state"] == "stopped" and not BOOTS)
# 7 기록의 op가 바뀌면 / 기록이 사라지면
fresh(); w, _ = armed(READ)
wd = watch.load(w["id"]); wd["record"] = "r-verify-gone"; watch._save(wd)          # the record it came from no longer exists
watch.tick(notify=False, log=lambda *a: None)
chk("근원 기록이 사라진 감시는 멈춤", watch.load(w["id"])["state"] == "stopped", finding=True)
# 8 상한: day_cap, total_day_cap, max_active, ttl
fresh(); w, _ = armed(READ)
wd = watch.load(w["id"]); wd["count_today"] = say.WATCH["day_cap"]; watch._save(wd)
watch.tick(notify=False, log=lambda *a: None)
chk("day_cap에 닿으면 쉼(resting), 부팅 없음", watch.load(w["id"])["state"] == "resting" and not BOOTS)
fresh(); w, _ = armed(READ); w2, _ = armed(READ)
wd = watch.load(w["id"]); wd["count_today"] = say.WATCH["total_day_cap"]; wd["next_at"] = time.time() + 999; watch._save(wd)
watch.tick(notify=False, log=lambda *a: None)
chk("total_day_cap에 닿으면 다른 감시도 부팅 없음", not BOOTS)
fresh(); ws = [armed(READ)[0] for _ in range(say.WATCH["max_active"])]; w5, prob = armed(READ)
chk(f"동시 무장 {say.WATCH['max_active']}개 다음은 거절", all(ws) and w5 is None and prob)
fresh(); w, _ = armed(READ)
wd = watch.load(w["id"]); wd["expires_at"] = time.time() - 1; watch._save(wd)
watch.tick(notify=False, log=lambda *a: None)
chk("ttl 지나면 만료, 부팅 없음", watch.load(w["id"])["state"] == "expired" and not BOOTS)
# 9 동시 무장 경합: 스레드 8개가 동시에 arm → max_active 넘지 않음
fresh(); CUR["src"] = READ
drafts = [watch.prepare("경합", {"check": "c", "every_s": 60})[0] for _ in range(8)]
res = []
ths = [threading.Thread(target=lambda d=d: res.append(watch.arm(d)[0])) for d in drafts]
[t.start() for t in ths]; [t.join() for t in ths]
chk(f"동시 arm 8회 → 무장 {say.WATCH['max_active']}개", sum(1 for r in res if r) == say.WATCH["max_active"])
# 10 동시 tick 경합: 같은 감시를 두 러너(서버 스레드 + CLI)가 → 부팅 1회
fresh(); w, _ = armed(READ)
ths = [threading.Thread(target=lambda: watch.tick(notify=False, log=lambda *a: None)) for _ in range(3)]
[t.start() for t in ths]; [t.join() for t in ths]
chk("러너 3개가 동시에 tick → 부팅 1회", len(BOOTS) == 1)
# 11 물을 사람이 없으면 무장 안 함
fresh(); evs = list(hub._arm_watch("시험", {"check": "c", "every_s": 60}, yes=False, no=False, ask=None))
chk("ask 없음·--yes 없음 → 무장 안 함", evs[-1].get("stage") == "stop" and not watch.all_watches())
# 12 '항상' 승인: 감시가 부른 행동에서 '항상'이라고 답해도 기록되면 안 됨 (from_watch)
import inspect
src_h = inspect.getsource(hub.pipeline)
chk("from_watch 행동에서 '항상' 답은 기록되지 않음", "from_watch" in src_h.split("REC.approve(rec")[0].splitlines()[-1] if "REC.approve(rec" in src_h else False, finding=True)
print(f"\n{good}/{n} watch-cap cases as expected")
sys.exit(0 if good == n else 1)
