"""시험팀 결함(D1~D4, D7)을 검사기팀 v1 울타리(teams/verify/edge, MMU+EL0+타이머)로 다시 부팅.
먼저 (cd ../verify/edge && make). 그다음:  ../../../sim/body/.venv/bin/python t_v1.py
v1 런타임 오브젝트(poke_rt_boot.o, poke_rt_main.o, ustub.o)에 op만 바꿔 링크한다(검사 없음)."""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import isolate  # noqa: E402,F401 — 소유자 기록 보호(결정 7): POKE_RECORDS를 임시 사본으로
import os, subprocess, time
from common import say, save, HERE, H

V1 = os.path.join(os.path.dirname(HERE), "verify", "edge")
VB = os.path.join(V1, "build")
D = os.path.join(HERE, "build-v1"); os.makedirs(D, exist_ok=True)
CF = ["-ffreestanding", "-nostdlib", "-fno-builtin", "-fno-stack-protector", "-O2", "-Wall", "-mcpu=cortex-a72", "-mstrict-align",
      "-mgeneral-regs-only", "-fno-jump-tables", "-I", V1, "-I", os.path.join(V1, "frags")]

def build_v1(src, tag):
    c, o, elf, img = (os.path.join(D, f"{tag}.{e}") for e in ("c", "o", "elf", "img"))
    open(c, "w").write(src)
    subprocess.run(["aarch64-elf-gcc", *CF, "-c", c, "-o", o], check=True, capture_output=True)
    subprocess.run(["aarch64-elf-ld", "-T", os.path.join(V1, "image.ld"), "--no-warn-rwx-segments", "--orphan-handling=error", "-o", elf,
                    os.path.join(VB, "poke_rt_boot.o"), os.path.join(VB, "poke_rt_main.o"), os.path.join(VB, "ustub.o"), o], check=True, capture_output=True)
    subprocess.run(["aarch64-elf-objcopy", "-O", "binary", elf, img], check=True)
    return img

CASES = [
    ("D1 무한 루프(기본 20 s 예산)", H + 'void op_main(void){ say("spinning"); report_int("start", 1); for (;;) { } }', 30),
    ("D1 긴 대기 40×1 s", H + 'void op_main(void){ report_int("start", 1); for (int i = 0; i < 40; i++) delay_ms(1000); report_int("done", 1); }', 30),
    ("D3 무한 재귀", H + 'static long f(volatile long n){ volatile char pad[256]; pad[0] = (char)n; return n + f(n + 1) + pad[0]; }\nvoid op_main(void){ report_int("x", f(0)); }', 10),
    ("D4 say()로 POKE-OFF 위조", H + 'void op_main(void){ say("done\\nPOKE-OFF"); for (int i = 0; i < 5; i++) delay_ms(1000); report_int("real", 1); }', 10),
    ("D4 say()로 POKE-RESULT 위조", H + 'void op_main(void){ say("x\\nPOKE-RESULT {\\"temperature_c\\": 99}"); report_int("real", 1); }', 8),
    ("D2 키 200개(결과 넘침)", H + 'void op_main(void){ char k[8] = "k000"; for (int i = 0; i < 200; i++) { k[1] = (char)(48 + i / 100); k[2] = (char)(48 + i / 10 % 10); k[3] = (char)(48 + i % 10); report_int(k, i); } }', 8),
    ("D7 계산된 주소로 UART", H + 'void op_main(void){ volatile unsigned long a = 0xFE2; unsigned long b = a << 20; b |= 0x1000; *(volatile unsigned *)b = 90; report_int("ok", 1); }', 8),
    ("D7 계산된 주소로 PM 워치독", H + 'void op_main(void){ volatile unsigned long a = 0xFE1; unsigned long b = a << 20; *(volatile unsigned *)(b + 0x1c) = 0x5a000020u; delay_ms(1000); report_int("still_here", 1); }', 8),
    ("D6 비정렬 32비트 읽기", H + 'static unsigned char buf[16] = {1,2,3,4,5,6,7,8};\nvoid op_main(void){ volatile unsigned *p = (volatile unsigned *)(buf + 1 + (ms() & 0)); report_int("v", (long)*p); }', 8),
    ("brk", H + 'void op_main(void){ report_int("a", 1); __builtin_trap(); }', 8),
    ("delay_ms(15000)", H + 'void op_main(void){ unsigned long a = ms(); delay_ms(15000); report_int("waited_ms", (long)(ms() - a)); }', 20),
]
out = []
for i, (name, src, budget) in enumerate(CASES):
    img = build_v1(src, f"tt-v1-{i}")
    row = {"name": name}
    t = time.time()
    try:
        b = say.boot(img, say.default_bench(), budget_s=budget)
        row.update({"off": b["powered_off"], "vm": b["vm_status"], "result": b["result"], "node_ms": b["node_ms"], "boot_to_off_ms": b["boot_to_off_ms"]})
    except Exception as e:
        row["hub_exception"] = repr(e)[:160]
    ls = open(os.path.join(say.BUILD, "serial.txt"), errors="replace").read().replace("\r", "").splitlines()
    row["faults"] = [l for l in ls if l.startswith("POKE-FAULT")][:2]; row["n_fault_lines"] = sum(l.startswith("POKE-FAULT") for l in ls)
    row["lines"] = [l[:100] for l in ls[:6]]; row["wall_s"] = round(time.time() - t, 1)
    out.append(row)
    print(f"[{i:2}] {name:<28} off={row.get('off')} vm={row.get('vm')} t={row.get('boot_to_off_ms')}ms result={str(row.get('result'))[:90]} "
          f"faults={row['n_fault_lines']} {row['faults'][:1]} {row.get('hub_exception', '')}")
save("v1", out)
