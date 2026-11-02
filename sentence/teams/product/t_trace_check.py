"""F1 흔적 대조 재발음 — 모델 없이: 견본 카드(옛 0x48 op)를 임시 허브로 가져와 0x4a 몸에서 실행하고,
재발음 op를 가짜 모델이 쓰게 해 세 판정을 확인한다. 소유자 기록·감시는 건드리지 않음.
    cd sentence && ../sim/body/.venv/bin/python teams/product/t_trace_check.py"""
import os, sys, json, tempfile
SENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, SENT); os.chdir(SENT)
os.environ["POKE_RECORDS"] = tempfile.mkdtemp(prefix="tc-records-"); os.environ["POKE_LEGACY_OPS"] = "0"
import say, hub, body, record as R, claude_cli

HEAD = '/* MANIFEST {"v":1,"uses":[%s],"max_s":2,"summary_ko":"시험"} */\n/* PARAMS [] */\n#include "rt.h"\n#include "tmp105.h"\n%s#define POKE_ADDR_TMP105 0x48\n#define POKE_ADDR_LED 0x60\n'
SAME = HEAD % ('{"fn":"tmp105_read_mC"}', "") + 'void op_main(void){ long t; if (tmp105_read_mC(POKE_ADDR_TMP105,&t)) { fail("x"); return; } report_fixed("temp_c", t, 3); }'
MORE = HEAD % ('{"fn":"tmp105_read_mC"}', "") + 'void op_main(void){ long t; if (tmp105_read_mC(POKE_ADDR_TMP105,&t)) { fail("x"); return; } report_fixed("celsius", t, 3); report_fixed("fahrenheit", t * 9 / 5 + 32000, 3); }'
OTHER = HEAD % ('{"fn":"tmp105_read_mC"},{"fn":"led_set","targets":[0],"end":"leave"}', '#include "led.h"\n') + 'void op_main(void){ long t; if (tmp105_read_mC(POKE_ADDR_TMP105,&t)) { fail("x"); return; } led_set(POKE_ADDR_LED, 0, 0); report_int("hot", t > 0); }'
SHIFT = HEAD % ('{"fn":"tmp105_read_mC"}', "") + 'void op_main(void){ long t; if (tmp105_read_mC(POKE_ADDR_TMP105,&t)) { fail("x"); return; } report_fixed("temp_k", t + 273150, 3); }'
EXTRA = HEAD % ('{"fn":"tmp105_read_mC"},{"fn":"mag_read"}', '#include "mag.h"\n#define POKE_ADDR_MAG 0x1e\n') + 'void op_main(void){ long t; int x, y, z; if (tmp105_read_mC(POKE_ADDR_TMP105,&t)) { fail("x"); return; } mag_read(POKE_ADDR_MAG, &x, &y, &z); report_fixed("temp_c", t, 3); report_int("mag_x", x); }'
CASES = [("같은 일", SAME, "match"), ("이름만 다른 결과(celsius·fahrenheit)", MORE, "match"),
         ("같은 답 + 자기 센서도 읽음", EXTRA, "partial"),
         ("값이 달라짐(켈빈만 보고 — 사람에게 다른 답)", SHIFT, "mismatch"), ("다른 일(LED 씀)", OTHER, "mismatch")]
body.load_body_file(os.path.join("teams", "product", "bodies", "tmp105-0x4a.json"))
good = 0
for name, src, want in CASES:
    for f in os.listdir(R.RECORDS):
        if f.endswith(".json"): os.remove(os.path.join(R.RECORDS, f))
    R.import_card(json.load(open("teams/product/cards/temp-old-0x48.json")))
    old_op = R.find("온도 알려줘")["op"]
    claude_cli.ask = lambda *a, src=src, **k: src
    evs = list(hub.pipeline("온도 알려줘", yes=True))
    tc = next((e for e in evs if e["stage"] == "trace_check"), {})
    r = R.find("온도 알려줘")
    kept = r["op"] == old_op
    ok = tc.get("verdict") == want and kept == (want == "mismatch")
    good += ok
    print(f"{'✓' if ok else '✗'} {name}: {tc.get('verdict')} (want {want}) · 이전 발음 유지={kept} · {tc.get('reasons')}")
print(f"\n{good}/{len(CASES)} trace-check cases as expected"); sys.exit(0 if good == len(CASES) else 1)
