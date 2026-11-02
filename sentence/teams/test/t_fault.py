"""실패 경로: op의 평범한 버그·실패·끝나지 않음 — 이미지 검사를 통과하는가, 노드가 보고하고 꺼지는가.
    ../../../sim/body/.venv/bin/python t_fault.py"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import isolate  # noqa: E402,F401 — 소유자 기록 보호(결정 7): POKE_RECORDS를 임시 사본으로
import time
from common import say, build_tt, save, qemu_leftovers, H

CASES = [
    # name, source, boot budget s, expectation (text)
    ("brk(__builtin_trap)", H + 'void op_main(void){ say("before"); report_int("a", 1); __builtin_trap(); report_int("b", 2); }', 8,
     "FAULT 보고 + error + off"),
    ("udf(정의 안 된 명령)", H + 'void op_main(void){ say("before"); __asm__ volatile(".inst 0x00000000"); }', 8, "FAULT + off (검사가 거절해도 됨)"),
    ("정렬 안 된 32비트 읽기(RAM)", H + 'static unsigned char buf[16] = {1,2,3,4,5,6,7,8};\n'
     'void op_main(void){ volatile unsigned *p = (volatile unsigned *)(buf + 1 + (ms() & 0)); report_int("v", (long)*p); }', 8,
     "MMU off = Device 메모리 → 정렬 오류 FAULT"),
    ("정렬 안 된 64비트 쓰기(스택)", H + 'void op_main(void){ unsigned char b[32]; volatile unsigned long *p = (volatile unsigned long *)(b + 3 + (ms() & 0)); *p = 7; report_int("v", (long)b[3]); }', 8,
     "FAULT"),
    ("0으로 나누기", H + 'void op_main(void){ volatile long z = 0; long a = 100; report_int("q", a / z); report_int("r", a % z); }', 8,
     "aarch64는 트랩 없음 → 조용히 0 (보고만 확인)"),
    ("널 포인터 읽기/쓰기", H + 'void op_main(void){ volatile unsigned *p = (volatile unsigned *)(ms() & 0); report_int("r0", *p); *p = 0xdead; report_int("r1", *p); }', 8,
     "MMU 없음 → 0번지는 RAM(armstub) → 조용히 성공?"),
    ("4GB 밖 주소 읽기(0x6_0000_0000)", H + 'void op_main(void){ volatile unsigned *p = (volatile unsigned *)0x600000000UL; report_int("v", *p); }', 8,
     "검사 범위(0xFC000000..4GB) 밖 — 검사 통과? 실행 시 abort?"),
    ("무한 재귀(스택 넘침 16KB)", H + 'static long f(volatile long n){ volatile char pad[256]; pad[0] = (char)n; return n + f(n + 1) + pad[0]; }\n'
     'void op_main(void){ report_int("x", f(0)); }', 10, "스택 아래 bss/data/text를 덮음 — 결과 불명"),
    ("끝나지 않는 루프", H + 'void op_main(void){ say("spinning"); for (;;) { } }', 6, "허브 예산(여기 6s)이 끊음, powered_off=False"),
    ("긴 대기(20 s 넘김, 예산 30s 기본)", H + 'void op_main(void){ say("long"); report_int("start", 1); for (int i = 0; i < 40; i++) delay_ms(1000); report_int("done", 1); }', 30,
     "기본 예산 30s에 끊김 — 부분 결과는?"),
    ("delay_ms(15000) 조용한 잘림", H + 'void op_main(void){ unsigned long a = ms(); delay_ms(15000); report_int("waited_ms", (long)(ms() - a)); }', 20,
     "15000 요청 → 실제 10000 (경고 없음)"),
    ("fail() 후 return", H + 'void op_main(void){ report_int("partial", 5); fail("sensor said no"); }', 8, "error 키 + status=failed + off"),
    ("fail() 후 계속 보고", H + 'void op_main(void){ fail("first"); report_int("after", 1); fail("second"); }', 8, "마지막 이유만 남음"),
    ("fail() 긴 이유(300자)+따옴표", H + 'void op_main(void){ fail("he said \\"no\\" ' + "x" * 300 + '"); }', 8, "127자로 잘림, JSON 유효"),
    ("say()로 POKE-OFF 위조", H + 'void op_main(void){ say("done\\nPOKE-OFF"); for (int i = 0; i < 5; i++) delay_ms(1000); report_int("real", 1); }', 10,
     "허브가 진짜 전원 종료 전에 off로 판정하는가"),
    ("say()로 POKE-RESULT 위조", H + 'void op_main(void){ say("x\\nPOKE-RESULT {\\"temperature_c\\": 99}"); report_int("real", 1); }', 8,
     "허브가 가짜 결과를 먼저 집는가"),
]

out = []
for i, (name, src, budget, expect) in enumerate(CASES):
    row = {"name": name, "expect": expect}
    try:
        img, obj, path, chk = build_tt(src, f"tt-fault-{i}")
        row["check_ok"] = chk["ok"]; row["check_problems"] = chk["problems"][:3]
    except RuntimeError as e:
        row["build_error"] = str(e)[:300]; out.append(row); print("✗ build", name, str(e)[:200]); continue
    t = time.time()
    try:
        b = say.boot(path, say.default_bench(), budget_s=budget)
    except Exception as e:
        import os
        lines = open(os.path.join(say.BUILD, "serial.txt"), errors="replace").read().replace("\r", "").splitlines()
        row.update({"hub_exception": repr(e)[:300], "last_lines": lines[-6:], "qemu_left": qemu_leftovers()})
        out.append(row); print(f"[{i:2}] {name:<32} HUB EXCEPTION {repr(e)[:150]} lines={lines[-4:]}"); continue
    row.update({"powered_off_hub": b["powered_off"], "vm_status": b["vm_status"], "result": b["result"],
                "faults": [l for l in b["lines"] if l.startswith("POKE-FAULT")], "end": [l for l in b["lines"] if l.startswith("POKE-END")],
                "last_lines": b["lines"][-6:], "boot_to_off_ms": b["boot_to_off_ms"], "wall_s": round(time.time() - t, 1),
                "qemu_left": qemu_leftovers()})
    out.append(row)
    print(f"[{i:2}] {name:<32} check={'ok' if chk['ok'] else 'REFUSED'} off={b['powered_off']} vm={b['vm_status']} "
          f"t={b['boot_to_off_ms']}ms result={b['result']} fault={row['faults'][:1]} end={row['end']}")
save("fault", out)
