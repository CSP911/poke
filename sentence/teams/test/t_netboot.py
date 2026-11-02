"""넷부팅 반복: 허브 TFTP 서버 + 가상 부트로더를 띄우고 여러 이미지를 연속으로 맡김(장애 이미지 포함).
    ../../../sim/body/.venv/bin/python t_netboot.py"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import isolate  # noqa: E402,F401 — 소유자 기록 보호(결정 7): POKE_RECORDS를 임시 사본으로
import os, sys, time, subprocess
from common import say, build_tt, save, summary, H, HERE

PORT = 16979
NB = os.path.join(say.HERE, "netboot")
logf = open(os.path.join(HERE, "results", "netboot-procs.log"), "w")
tftpd = subprocess.Popen([sys.executable, "-u", os.path.join(NB, "tftpd.py"), "--port", str(PORT)], stdout=logf, stderr=subprocess.STDOUT)
vboot = subprocess.Popen([sys.executable, "-u", "vboot.py", "--port", str(PORT)], cwd=NB, stdout=logf, stderr=subprocess.STDOUT)
time.sleep(1.5)

def img_of(key, tag):
    return build_tt(open(os.path.join(say.BUILD, "ops", key + ".c")).read(), tag)[2]

seq = [("온도", img_of("600ad0ab6d86", "tt-nb-temp")), ("LED3", img_of("40c84902bd30", "tt-nb-led3")),
       ("자기장", img_of("785d3c5a0df8", "tt-nb-mag")), ("시간", img_of("bc0e2112cd8f", "tt-nb-rtc")),
       ("임계 LED0", img_of("eba0bb1b8762", "tt-nb-thr")),
       ("brk 장애", build_tt(H + 'void op_main(void){ report_int("a",1); __builtin_trap(); }', "tt-nb-brk")[2]),
       ("LED0~3", img_of("4b91f05b8a15", "tt-nb-led03")), ("온도(재)", img_of("600ad0ab6d86", "tt-nb-temp")),
       ("무한 루프", build_tt(H + 'void op_main(void){ for(;;){} }', "tt-nb-hang")[2]),
       ("무한 루프 다음 온도", img_of("600ad0ab6d86", "tt-nb-temp"))]
rows = []
try:
    for name, path in seq:
        t0 = time.time()
        try:
            b = say.boot_netboot_twin(path, budget_s=60)
            rows.append({"name": name, "ok_off": b.get("powered_off"), "result": b.get("result"), "fetched_ms": b.get("fetched_ms"),
                         "boot_to_off_ms": b.get("boot_to_off_ms"), "hub_wall_ms": round((time.time() - t0) * 1000), "boot_n": b.get("boot")})
        except Exception as e:
            rows.append({"name": name, "error": repr(e)[:200], "hub_wall_ms": round((time.time() - t0) * 1000)})
        print(rows[-1])
        time.sleep(1.0)                                     # 사이에 부트로더는 묻고, 허브는 답하지 않아야 함
finally:
    vboot.terminate(); tftpd.terminate()
    time.sleep(0.5); vboot.kill(); tftpd.kill(); logf.close()
log = open(os.path.join(HERE, "results", "netboot-procs.log")).read()
out = {"rows": rows, "idle_ignored_requests": log.count("idle: ignoring"), "sentences_consumed": log.count("sentence consumed"),
       "fetched_ms": summary([r.get("fetched_ms") for r in rows]), "hub_wall_ms": summary([r.get("hub_wall_ms") for r in rows])}
save("netboot", out)
print({k: v for k, v in out.items() if k != "rows"})
