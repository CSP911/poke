#!/usr/bin/env python3
"""Sentence boot through U-Boot's TFTP — for edges whose boot loader is U-Boot rather than a PXE ROM.

The twin: QEMU's PC with U-Boot as its firmware (build-ext/u-boot-poke-x86.rom, qemu-x86_defconfig built with
i686-elf-gcc). Its boot command: DHCP, then ask the hub's tftpd (port 16969) for sentence-x86.elf in a loop;
when the hub answers (a sentence is pending), `bootelf` runs it — the same x86 sentence image as the PXE path
(edge/sentence-x86). The image ends by zeroing what it used and resetting the PC, which comes back up in
U-Boot asking again. Between sentences the PC runs only U-Boot (not ours).

    ../sim/body/.venv/bin/python uboot.py selftest

Building the ROM (once): see build-ext/ (U-Boot master, qemu-x86_defconfig, EFI off, BOOTDELAY 0,
BOOTCOMMAND as above; on an arm64 Mac: GNU make, bash ≥ 4.2, swig, a system-Python venv with setuptools,
HOSTLDFLAGS="-undefined dynamic_lookup", and arch/x86/include/asm/byteorder.h kept out of host tools).
"""
import os, sys, time, subprocess, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import x86

ROM = os.path.join(os.path.dirname(HERE), "build-ext", "u-boot-poke-x86.rom")
PORT = 16969                                          # baked into the ROM's boot command (tftpdstp)

class Board:
    def __init__(self):
        self.root = tempfile.mkdtemp(dir=x86.BUILD)
        self.ser = os.path.join(x86.BUILD, f"uboot-{os.getpid()}.txt"); open(self.ser, "w").close()
        self.srv = subprocess.Popen([sys.executable, os.path.join(HERE, "netboot", "tftpd.py"), "--port", str(PORT), "--root", self.root],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.t0 = time.time()
        self.q = subprocess.Popen(["qemu-system-x86_64", "-M", "pc", "-m", "256", "-bios", ROM, "-display", "none",
                                   "-serial", f"file:{self.ser}", "-netdev", "user,id=n0", "-device", "e1000,netdev=n0", "-monitor", "none"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while "TFTP from server" not in self.text() and time.time() - self.t0 < 30: time.sleep(0.05)
        self.up_s = round(time.time() - self.t0, 2)

    def text(self):
        return open(self.ser, errors="replace").read().replace("\r", "")

    def say(self, elf, budget_s=60):
        t = self.text(); n_off, n_boot = t.count("POKE-OFF"), t.count("POKE-BOOT")
        tp = time.time()
        subprocess.run([sys.executable, os.path.join(HERE, "netboot", "tftpd.py"), "--pend", elf, "--root", self.root], check=True, capture_output=True)
        tb = None
        while time.time() - tp < budget_s:
            t = self.text()
            if tb is None and t.count("POKE-BOOT") > n_boot: tb = time.time() - tp
            if t.count("POKE-OFF") > n_off: break
            time.sleep(0.02)
        done = time.time() - tp
        n_tftp, t1 = t.count("TFTP from server"), time.time()
        while self.text().count("TFTP from server") <= n_tftp and time.time() - t1 < 30: time.sleep(0.05)
        res = ([l[12:] for l in t.splitlines() if l.startswith("POKE-RESULT ")] or [None])[-1]
        return {"result": res, "start_s": round(tb, 2) if tb else None, "done_s": round(done, 2), "back_s": round(time.time() - t1, 2)}

    def close(self):
        self.q.kill(); self.srv.terminate()

def selftest():
    if not os.path.exists(ROM):
        print(f"no U-Boot ROM at {ROM} — build it first (see the docstring)"); return False
    b, ok = Board(), 0
    tests = [('void op_main(void){ say("U-Boot이 받은 문장"); long n=0; for(long i=1;i<=1000000;i++) n+=i%7==0; report_int("multiples_of_7", n); }', "142857"),
             ('void op_main(void){ long n=0; for(long i=2;i<200000;i++){int p=1; for(long d=2;d*d<=i;d++) if(i%d==0){p=0;break;} n+=p;} report_int("primes", n); }', "17984"),
             ('void op_main(void){ volatile int *p=0; report_int("x",*p); }', "outside its pages")]
    try:
        print(f"U-Boot asking the hub {b.up_s} s after power-on")
        for i, (body, want) in enumerate(tests):
            r = b.say(x86.build('#include "rt.h"\n' + body, f"ub-{i}"))
            good = bool(r["result"]) and want in r["result"]; ok += good
            print(f"{'✓' if good else '✗'} sentence {i + 1}: pending → our code {r['start_s']} s · done {r['done_s']} s · "
                  f"back in U-Boot asking {r['back_s']} s later · {r['result']}")
    finally:
        b.close()
    print(f"\nU-Boot selftest {ok}/{len(tests)}")
    return ok == len(tests)

if __name__ == "__main__":
    sys.exit(0 if (sys.argv[1:] == ["selftest"] and selftest()) else (print(__doc__) or 1))
