"""T12 흔적 대조 평가(제품 F1, trace_check.compare): 카드의 흔적 = 기억된 op의 실제 실행(결과·EFFECTS). 그 위에
손으로 쓴 '다시 발음한 op'들(모델 0)을 부팅해 판정이 맞는지 — 같은 일(키 이름만 다름)은 match, 다른 일은 mismatch/partial.
    cd sentence && ../sim/body/.venv/bin/python teams/test/e12_trace.py"""
import os, sys, json, time
HERE = os.path.dirname(os.path.abspath(__file__)); SENT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, SENT)
import isolate  # noqa: F401
os.environ["POKE_CLAUDE"] = "/nonexistent/claude-blocked"
os.chdir(SENT)
import say, trace_check as TC  # noqa: E402

def mf(uses, summary):
    return '/* MANIFEST ' + json.dumps({"v": 1, "uses": uses, "max_s": 2, "summary_ko": summary}, ensure_ascii=False) + ' */\n'
LEDW = [{"fn": "led_set", "targets": [3], "end": "leave"}]
LED = '#include "rt.h"\n#include "led.h"\n'
TMP = '#include "rt.h"\n#include "tmp105.h"\n'
def ledop(body, uses=LEDW): return mf(uses, "LED") + LED + "void op_main(void){ " + body + " }"
def tmpop(body): return mf([{"fn": "tmp105_read_mC"}], "온도") + TMP + "void op_main(void){ long t; if (tmp105_read_mC(POKE_ADDR_TMP105, &t)) { fail(\"x\"); return; } " + body + " }"

CASES = {   # card sentence → [(name, op source, acceptable verdicts, what it does)]
    "LED 3번 켜줘": [
        ("같은 일, 키 이름만 다름", ledop('if (led_set(POKE_ADDR_LED, 3, 0)) { fail("x"); return; } report_int("which", 3); report_int("is_on", 1);'), {"match", "partial"}, "same"),
        ("다른 LED(4번)를 켬", ledop('if (led_set(POKE_ADDR_LED, 4, 0)) { fail("x"); return; } report_int("led", 4); report_int("led_on", 1);', [{"fn": "led_set", "targets": [4], "end": "leave"}]), {"mismatch"}, "wrong"),
        ("3번 켜고 9번도 켬(여분 행동)", ledop('if (led_set(POKE_ADDR_LED, 3, 0) || led_set(POKE_ADDR_LED, 9, 0)) { fail("x"); return; } report_int("led", 3); report_int("led_on", 1);', [{"fn": "led_set", "targets": [3, 9], "end": "leave"}]), {"mismatch"}, "wrong"),
        ("켜지 않고 켰다고 보고만", mf([{"fn": "led_inputs"}], "LED") + LED + 'void op_main(void){ unsigned p; if (led_inputs(POKE_ADDR_LED, &p)) { fail("x"); return; } report_int("led", 3); report_int("led_on", 1); }', {"mismatch"}, "wrong"),
        ("3번을 깜빡이게(모드 다름)", ledop('if (led_set(POKE_ADDR_LED, 3, 2)) { fail("x"); return; } report_int("led", 3); report_int("led_on", 1);'), {"mismatch"}, "wrong"),
        ("실패", ledop('fail("led busy");'), {"mismatch"}, "wrong"),
    ],
    "온도 알려줘": [
        ("같은 일, 키 이름만 다름", tmpop('report_fixed("celsius", t, 3);'), {"match"}, "same"),
        ("같은 일 + mC도 함께", tmpop('report_fixed("temp_c", t, 3); report_int("temp_mc", t);'), {"match"}, "same"),
        ("화씨로만 보고(단위 바뀜)", tmpop('report_fixed("temp_f", t * 9 / 5 + 32000, 3);'), {"mismatch", "partial"}, "wrong"),
        ("고정값 23.75를 보고(센서 안 읽음)", mf([], "온도") + '#include "rt.h"\nvoid op_main(void){ report_fixed("temp_c", 23750, 3); }', {"mismatch", "partial"}, "wrong"),
        ("온도 읽고 LED도 켬(여분 행동)", mf([{"fn": "tmp105_read_mC"}, {"fn": "led_set", "targets": [0], "end": "leave"}], "온도") + TMP + '#include "led.h"\nvoid op_main(void){ long t; if (tmp105_read_mC(POKE_ADDR_TMP105, &t)) { fail("x"); return; } led_set(POKE_ADDR_LED, 0, 0); report_fixed("temp_c", t, 3); }', {"mismatch"}, "wrong"),
    ],
}

def run(src, tag):
    src = __import__("hub").with_hub_consts(src)
    _, _, path = say.build(src, tag)
    b = say.boot(path, say.default_bench(), budget_s=10)
    return b, path

def main():
    rows = []
    for sentence, cands in CASES.items():
        op = say.make_op(sentence, log=lambda *a: None)
        b0 = say.boot(op["path"], say.default_bench(), budget_s=10)
        ref = [{"result": b0["result"], "fx": TC.fx_summary(b0, op["path"]), "body": "B", "bench": None, "ok": True}]
        print(f"card {sentence!r}: ref result {b0['result']} fx {ref[0]['fx']}")
        for i, (name, src, want, kind) in enumerate(cands):
            try:
                b, path = run(src, f"tt-e12-{abs(hash(sentence)) % 1000}-{i}")
                v = TC.compare({}, b.get("result"), TC.fx_summary(b, path), body_fp="B", bench=None, ref=ref)
            except Exception as e:
                v, b = {"verdict": "ERROR", "reasons": [f"{type(e).__name__}: {str(e)[:120]}"]}, {"result": None}
            ok = v["verdict"] in want
            rows.append({"card": sentence, "case": name, "kind": kind, "want": sorted(want), "got": v["verdict"], "ok": ok,
                         "result": b.get("result"), "reasons": v.get("reasons")})
            print(f"  {'✓' if ok else '✗'} {name:<28} → {v['verdict']:<9} (want {'/'.join(sorted(want))}) result={json.dumps(b.get('result'), ensure_ascii=False)[:60]} | {'; '.join(v.get('reasons') or [])[:150]}")
    n = len(rows); same = [r for r in rows if r["kind"] == "same"]; wrong = [r for r in rows if r["kind"] == "wrong"]
    adopted_wrong = [r for r in wrong if r["got"] in ("match", "partial", "unknown")]
    print(f"\n{sum(r['ok'] for r in rows)}/{n} as wanted · same-work kept {sum(r['got'] in ('match', 'partial') for r in same)}/{len(same)} · "
          f"different work refused (mismatch) {sum(r['got'] == 'mismatch' for r in wrong)}/{len(wrong)} · different work ADOPTED {len(adopted_wrong)}: {[r['card'] + '/' + r['case'] for r in adopted_wrong]}")
    json.dump(rows, open(os.path.join(HERE, "results", f"e12-{time.strftime('%Y%m%d-%H%M%S')}.json"), "w"), indent=1, ensure_ascii=False)

if __name__ == "__main__":
    main()
