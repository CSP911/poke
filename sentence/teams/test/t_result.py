"""결과 줄 경계(1024 B 버퍼)와 보고 함수 경계값, 그리고 몇 가지 추가 탐침.
    ../../../sim/body/.venv/bin/python t_result.py"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import isolate  # noqa: E402,F401 — 소유자 기록 보호(결정 7): POKE_RECORDS를 임시 사본으로
import os, json
from common import say, build_tt, save, H

def serial_lines():
    return open(os.path.join(say.BUILD, "serial.txt"), errors="replace").read().replace("\r", "").splitlines()

CASES = [
    ("키 200개 report_int", H + 'void op_main(void){ char k[8] = "k000"; for (int i = 0; i < 200; i++) { k[1] = (char)(48 + i / 100); k[2] = (char)(48 + i / 10 % 10); k[3] = (char)(48 + i % 10); report_int(k, i); } }'),
    ("report_str 2000자", H + 'static char s[2001];\nvoid op_main(void){ for (int i = 0; i < 2000; i++) s[i] = (char)(97 + i % 26); s[2000] = 0; report_str("long", s); }'),
    ("거의 꽉 참 + fail()", H + 'static char s[1001];\nvoid op_main(void){ for (int i = 0; i < 1000; i++) s[i] = 120; s[1000] = 0; report_str("s", s); fail("sensor gone"); }'),
    ("1000자 + 정수 하나", H + 'static char s[1011];\nvoid op_main(void){ for (int i = 0; i < 1010; i++) s[i] = 120; s[1010] = 0; report_str("s", s); report_int("n", 12345); }'),
    ("report_fixed 음수 소수", H + 'void op_main(void){ report_fixed("a", -500, 3); report_fixed("b", -1, 1); report_fixed("c", 7, 0); }'),
    ("report_fixed LONG_MIN", H + 'void op_main(void){ report_fixed("m", (-9223372036854775807L - 1), 3); }'),
    ("report_fixed decimals=-1", H + 'void op_main(void){ report_fixed("d", 5, -1); }'),
    ("report_int LONG_MIN", H + 'void op_main(void){ report_int("m", (-9223372036854775807L - 1)); }'),
    ("report_str 한국어(UTF-8)", H + 'void op_main(void){ report_str("msg", "온도 정상"); say("한국어 say"); }'),
    ("같은 키 두 번", H + 'void op_main(void){ report_int("t", 1); report_int("t", 2); }'),
    ("보고 없음", H + 'void op_main(void){ }'),
    # 추가 탐침
    ("0번지 읽기/쓰기(비상수)", H + 'void op_main(void){ volatile unsigned *p = (volatile unsigned *)(ms() >> 60); unsigned a = *p; *p = 0xdead; report_int("r0", a); report_int("r1", *p); }'),
    ("검사 회피: 계산된 주소로 UART 쓰기", H + 'void op_main(void){ volatile unsigned long a = 0xFE2; unsigned long b = a << 20; b |= 0x1000; *(volatile unsigned *)b = 90; *(volatile unsigned *)b = 10; report_int("ok", 1); }'),
    ("검사 회피: 35비트 별칭 0x4_7E20_1000에 UART 쓰기", H + 'void op_main(void){ volatile unsigned *u = (volatile unsigned *)0x47E201000UL; *u = 89; *u = 10; report_int("ok", 1); }'),
    ("검사 회피: 계산된 주소로 PM 워치독(즉시 리셋)", H + 'void op_main(void){ volatile unsigned long a = 0xFE1; unsigned long b = a << 20; *(volatile unsigned *)(b + 0x24) = 0x5a000000u | 10; *(volatile unsigned *)(b + 0x1c) = 0x5a000020u; for (int i = 0; i < 100; i++) delay_ms(10); report_int("still_here", 1); }'),
]

out = []
for i, (name, src) in enumerate(CASES):
    row = {"name": name}
    img, obj, path, chk = build_tt(src, f"tt-result-{i}")
    row["check_ok"] = chk["ok"]; row["check_problems"] = chk["problems"][:2]
    try:
        b = say.boot(path, say.default_bench(), budget_s=8)
        row.update({"result": b["result"], "off": b["powered_off"], "vm": b["vm_status"]})
    except Exception as e:
        row["hub_exception"] = repr(e)[:200]
    ls = serial_lines()
    rl = next((l for l in ls if l.startswith("POKE-RESULT")), "")
    row["result_line_len"] = len(rl); row["result_line_tail"] = rl[-60:]
    row["other_lines"] = [l[:120] for l in ls if not l.startswith(("POKE-RESULT", "POKE-BOOT"))][:6]
    out.append(row)
    print(f"[{i:2}] {name:<40} check={'ok' if chk['ok'] else 'REFUSED: ' + '; '.join(chk['problems'])[:80]} "
          f"{'EXC ' + row['hub_exception'] if 'hub_exception' in row else 'off=%s vm=%s' % (row['off'], row['vm'])} "
          f"len={len(rl)} tail={rl[-50:]!r} other={row['other_lines'][:3]}")
save("result", out)
