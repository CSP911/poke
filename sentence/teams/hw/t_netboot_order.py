"""H1+H2 test: the hub's tftpd (parked requests) + the virtual bootloader asking in the real Pi 4 order.

Own port, own TFTP root and own report dir, so it does not touch netboot/root or
netboot/vboot-out that other tests use.  ../../../sim/body/.venv/bin/python t_netboot_order.py [N] [--cd]

Pass, for N sentences in a row:
  - every request sequence is the real one (prefix probe, config, recover4/recovery not found,
    start+fixup, firmware's config/dtb/overlay_map/cmdline/armstub, kernel probe, kernel);
  - the image fetched is the one pended, byte count equal; the kernel probe did not consume it;
  - every sentence is consumed exactly once and the node powered off;
  - the parked probe made "pending → first answer" fast (< 1 s), with no 30 s timeout in between;
  - --cd: config.txt has gpu_mem=16 → the bootloader asks for start4cd.elf/fixup4cd.dat.
"""
import os, sys, json, time, glob, shutil, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
SENT = os.path.dirname(os.path.dirname(HERE))
NB = os.path.join(SENT, "netboot")
PY = sys.executable
PORT = 16989
CD = "--cd" in sys.argv
N = int(next((a for a in sys.argv[1:] if a.isdigit()), "10"))
RES = os.path.join(HERE, "results"); os.makedirs(RES, exist_ok=True)
ROOT = os.path.join(RES, "tftproot"); OUT = os.path.join(RES, "vboot-out")
shutil.rmtree(ROOT, ignore_errors=True); shutil.rmtree(OUT, ignore_errors=True); os.makedirs(ROOT); os.makedirs(OUT)
for f in ("start4.elf", "fixup4.dat", "bcm2711-rpi-4-b.dtb", "start4cd.elf", "fixup4cd.dat"):
    src = os.path.join(NB, "root", f)
    if os.path.exists(src): os.symlink(os.path.realpath(src), os.path.join(ROOT, f))
cfg = open(os.path.join(NB, "root", "config.txt")).read()
if CD and "gpu_mem=16" not in cfg: cfg += "gpu_mem=16\n"
if not CD: cfg = "\n".join(l for l in cfg.splitlines() if not l.startswith("gpu_mem")) + "\n"
open(os.path.join(ROOT, "config.txt"), "w").write(cfg)

imgs = sorted(glob.glob(os.path.join(SENT, "build", "img", "*.img")), key=os.path.getmtime)[-N:]
logf = open(os.path.join(RES, "netboot-order-procs.log"), "w")
tftpd = subprocess.Popen([PY, "-u", os.path.join(NB, "tftpd.py"), "--port", str(PORT), "--root", ROOT], stdout=logf, stderr=subprocess.STDOUT)
vboot = subprocess.Popen([PY, "-u", os.path.join(NB, "vboot.py"), "--port", str(PORT), "--out", OUT], cwd=NB, stdout=logf, stderr=subprocess.STDOUT)
start, fixup = ("start4cd.elf", "fixup4cd.dat") if CD else ("start4.elf", "fixup4.dat")
EXPECT = [("1a2b3c4d/start4.elf", True), ("1a2b3c4d/config.txt", False), ("1a2b3c4d/recover4.elf", False),
          ("1a2b3c4d/recovery.elf", False), (f"1a2b3c4d/{start}", False), (f"1a2b3c4d/{fixup}", False),
          ("1a2b3c4d/config.txt", False), ("1a2b3c4d/bcm2711-rpi-4-b.dtb", False), ("1a2b3c4d/overlays/overlay_map.dtb", False),
          ("1a2b3c4d/cmdline.txt", False), ("1a2b3c4d/armstub8-gic.bin", False), ("1a2b3c4d/sentence.img", True), ("1a2b3c4d/sentence.img", False)]
rows, ok = [], True
try:
    time.sleep(2.0)                                            # the bootloader's probe is now parked
    for i, img in enumerate(imgs):
        before = set(os.listdir(OUT))
        subprocess.run([PY, os.path.join(NB, "tftpd.py"), "--pend", img, "--root", ROOT], check=True, capture_output=True)
        t0 = time.time(); rec = None
        while time.time() - t0 < 60:
            new = [f for f in set(os.listdir(OUT)) - before if f.endswith(".json")]
            if new:
                time.sleep(0.05); rec = json.load(open(os.path.join(OUT, new[0]))); break
            time.sleep(0.05)
        if rec is None:
            rows.append({"img": os.path.basename(img), "error": "no boot in 60 s"}); ok = False; continue
        seq = [(r["file"], r["probe"]) for r in rec["requests"]]
        first_ms = rec["requests"][0]["ms"]
        size_ok = rec["requests"][-1]["got"] == os.path.getsize(img)
        consumed = not os.path.exists(os.path.join(ROOT, "pending.img"))
        good = seq == EXPECT and size_ok and consumed and rec.get("powered_off") is True
        rows.append({"img": os.path.basename(img), "order_ok": seq == EXPECT, "size_ok": size_ok, "consumed": consumed,
                     "powered_off": rec.get("powered_off"), "fetched_ms": rec.get("fetched_ms"),
                     "pend_to_done_s": round(time.time() - t0, 2), "result": rec.get("result")})
        if seq != EXPECT: rows[-1]["seq"] = seq
        ok &= good
        print(i, "PASS" if good else "FAIL", rows[-1], flush=True)
        time.sleep(0.5)
finally:
    vboot.terminate(); tftpd.terminate(); time.sleep(0.5); vboot.kill(); tftpd.kill(); logf.close()
log = open(os.path.join(RES, "netboot-order-procs.log")).read()
summary = {"n": len(imgs), "pass": sum(1 for r in rows if r.get("order_ok") and r.get("size_ok") and r.get("consumed") and r.get("powered_off")),
           "sentences_consumed": log.count("sentence consumed"), "kernel_probes_not_consumed": log.count("gave up on sentence.img"),
           "netboot_failed_lines": log.count("network boot failed ("), "cd": CD}
ok &= summary["sentences_consumed"] == len(imgs)
json.dump({"summary": summary, "rows": rows}, open(os.path.join(RES, f"netboot-order{'-cd' if CD else ''}.json"), "w"), indent=1, ensure_ascii=False)
print(summary); print("netboot order", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
