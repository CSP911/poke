"""검사 오탐 탐침(부팅 없음): 평범한 음수 int 상수가 '주변장치 주소'로 오인되는가.
    ../../../sim/body/.venv/bin/python t_check_fp.py"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import isolate  # noqa: E402,F401 — 소유자 기록 보호(결정 7): POKE_RECORDS를 임시 사본으로
from common import build_tt, save, H

CASES = [
    ("int idx = -1 (못 찾음 표시)", H + '#include "led.h"\nvoid op_main(void){ int found = -1; unsigned p; if (led_inputs(0x60, &p)) { fail("x"); return; } for (int i = 0; i < 16; i++) if (!((p >> i) & 1)) { found = i; break; } report_int("first_on", found); }'),
    ("int 하한 -40 클램프", H + '#include "tmp105.h"\nstatic int clamp(int v){ if (v < -40) return -40; if (v > 125) return 125; return v; }\nvoid op_main(void){ long t; if (tmp105_read_mC(0x48, &t)) { fail("x"); return; } report_int("t", clamp((int)(t / 1000))); }'),
    ("unsigned mask = 0xFFFFFFFF", H + 'void op_main(void){ volatile unsigned m = 0xFFFFFFFFu; report_int("m", (long)(m & 0xff)); }'),
    ("음수 int 테이블(.rodata)", H + 'static const int off[4] = { -1, 0, 1, -2 };\nvoid op_main(void){ long s = 0; for (int i = 0; i < 4; i++) s += off[ms() & 3]; report_int("s", s); }'),
    ("long -40 (64비트, 대조군)", H + 'void op_main(void){ volatile long v = -40; report_int("v", v); }'),
]
out = []
for i, (name, src) in enumerate(CASES):
    _, _, _, c = build_tt(src, f"tt-checkfp-{i}")
    out.append({"name": name, "ok": c["ok"], "problems": c["problems"][:2]})
    print(f"{'passed ' if c['ok'] else 'REFUSED'} {name:<32} {'; '.join(c['problems'])[:140]}")
save("check_fp", out)
