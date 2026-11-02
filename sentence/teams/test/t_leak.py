"""say.boot()가 실험대 설정(qom-set)에서 예외를 내면 QEMU가 남는가(재현용).
    ../../../sim/body/.venv/bin/python t_leak.py"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import isolate  # noqa: E402,F401 — 소유자 기록 보호(결정 7): POKE_RECORDS를 임시 사본으로
from common import say, build_tt, save, qemu_leftovers, H
_, _, path, _ = build_tt(H + 'void op_main(void){ report_int("x", 1); }', "tt-leak")
before = qemu_leftovers()
try:
    say.boot(path, {"t1 temperature": 128000}, budget_s=5)
    exc = None
except Exception as e:
    exc = repr(e)[:150]
after = qemu_leftovers()
leaked = [p for p in after if p not in before]
print({"exception": exc, "leaked_qemu_pids": leaked})
save("leak", {"exception": exc, "leaked_qemu_pids": leaked})
import os, signal
for p in leaked: os.kill(int(p), signal.SIGKILL)                 # 시험 뒤 정리
