#!/usr/bin/env python3
"""Sentence boot on an ESP32-C3 through its ROM download mode — no flash, nothing of ours on the chip.

The chip's strapping pins hold it in the ROM's serial download mode (the twin: Espressif's QEMU with
strap_mode 0x02). The hub writes the sentence's image straight into RAM with the ROM loader
(esptool load_ram), the ROM jumps to it, the runtime runs the op, reports on UART0, zeroes the RAM the run
used and resets the chip — which, still strapped, comes back up in the ROM waiting for the next image.

    ../build-ext/espvenv/bin/python esp32.py selftest          # three images in a row on one chip
"""
import os, sys, re, json, time, shutil, socket, subprocess, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EDGE = os.path.join(ROOT, "edge", "sentence-esp32c3")
BUILD = os.path.join(HERE, "build", "esp32c3")
TC = os.path.expanduser("~/.espressif/tools/riscv32-esp-elf/esp-14.2.0_20241119/riscv32-esp-elf/bin")
CC, OBJ = os.path.join(TC, "riscv32-esp-elf-gcc"), os.path.join(TC, "riscv32-esp-elf-nm")
QEMU = os.path.expanduser("~/.espressif/tools/qemu-riscv32/esp_develop_9.0.0_20240606/qemu/bin/qemu-system-riscv32")
CFLAGS = ["-march=rv32imc_zicsr", "-mabi=ilp32", "-ffreestanding", "-nostdlib", "-fno-builtin", "-fno-stack-protector",
          "-Os", "-Wall", "-I", EDGE]

def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[-1500:])
    return r.stdout

def build(op_src, tag):
    """op C source → (ESP RAM image .bin, ELF)."""
    os.makedirs(BUILD, exist_ok=True)
    w = tempfile.mkdtemp(prefix=".b-", dir=BUILD)
    try:
        open(os.path.join(w, "op.c"), "w").write(op_src)
        objs = []
        for src, o in ((os.path.join(EDGE, "boot.S"), "boot.o"), (os.path.join(EDGE, "rt.c"), "rt.o"), (os.path.join(w, "op.c"), "op.o")):
            sh([CC, *CFLAGS, "-c", src, "-o", os.path.join(w, o)]); objs.append(os.path.join(w, o))
        elf = os.path.join(BUILD, f"{tag}.elf")
        libgcc = sh([CC, "-march=rv32imc_zicsr", "-mabi=ilp32", "-print-libgcc-file-name"]).strip()
        sh([CC, *CFLAGS, "-T", os.path.join(EDGE, "image.ld"), "-Wl,--no-warn-rwx-segments", "-o", elf, *objs, libgcc])
        binf = os.path.join(BUILD, f"{tag}.bin")
        sh([sys.executable, "-m", "esptool", "--chip", "esp32c3", "elf2image", "--ram-only-header", "-o", binf, elf])
        return binf, elf
    finally:
        shutil.rmtree(w, ignore_errors=True)

class Chip:
    """One emulated ESP32-C3, strapped for download mode, kept powered between sentences."""
    def __init__(self):
        self.port = 30000 + int.from_bytes(os.urandom(2), "little") % 20000
        self.qmp = self.port + 1
        self.q = subprocess.Popen([QEMU, "-M", "esp32c3", "-nographic", "-global", "driver=esp32c3.gpio,property=strap_mode,value=0x02",
                                   "-serial", f"tcp::{self.port},server,nowait", "-monitor", "none",
                                   "-qmp", f"tcp:127.0.0.1:{self.qmp},server=on,wait=off"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)

    def say(self, binf, budget_s=30):
        """ROM loader: load the image into RAM and run it; read the node's lines until POKE-OFF."""
        from esptool.cmds import detect_chip, load_ram
        import io, contextlib
        t0 = time.time()
        with contextlib.redirect_stdout(io.StringIO()):
            esp = detect_chip(f"socket://localhost:{self.port}", connect_mode="no-reset")
            t_conn = time.time()
            load_ram(esp, binf)
        t_jump = time.time()
        buf, lines = b"", []
        esp._port.timeout = 0.05
        while time.time() - t_jump < budget_s:
            buf += esp._port.read(4096)
            if b"POKE-OFF" in buf: break
        t_off = time.time()
        esp._port.close()
        text = buf.decode(errors="replace").replace("\r", "")
        lines = [l for l in text.splitlines() if l.startswith("POKE-")]
        res = next((json.loads(l[12:]) for l in reversed(lines) if l.startswith("POKE-RESULT ")), None)
        return {"lines": lines, "result": res, "off": any(l.startswith("POKE-OFF") for l in lines),
                "connect_s": round(t_conn - t0, 2), "load_s": round(t_jump - t_conn, 2), "run_s": round(t_off - t_jump, 2)}

    def close(self):
        self.q.kill(); self.q.wait()

SELF = [("primes", 'void op_main(void){ say("ESP32-C3 RAM에서 실행"); long n=0; for(long i=2;i<20000;i++){int p=1; for(long d=2;d*d<=i;d++) if(i%d==0){p=0;break;} n+=p;} report_int("primes_below_20000", n); }',
         lambda r: r == {"primes_below_20000": 2262}),
        ("delay", 'void op_main(void){ unsigned long a=ms(); delay_ms(300); report_int("waited_ms", (long)(ms()-a)); }',
         lambda r: r and 250 <= r.get("waited_ms", 0) <= 450),
        ("again", 'void op_main(void){ report_str("after", "two resets"); }', lambda r: r == {"after": "two resets"})]

def selftest():
    chip, ok = Chip(), 0
    try:
        for name, body, want in SELF:
            binf, elf = build('#include "rt.h"\n' + body, f"self-{name}")
            b = chip.say(binf)
            good = b["off"] and want(b["result"])
            ok += good
            print(f"{'✓' if good else '✗'} {name:<7} image {os.path.getsize(binf)} B · ROM sync {b['connect_s']} s · load {b['load_s']} s · "
                  f"run→off {b['run_s']} s · {json.dumps(b['result'], ensure_ascii=False)}")
            for l in b["lines"]:
                if l.startswith(("POKE-SAY", "POKE-FAULT")): print("    ", l)
    finally:
        chip.close()
    print(f"\nesp32c3 selftest {ok}/{len(SELF)}")
    return ok == len(SELF)

if __name__ == "__main__":
    if sys.argv[1:] == ["selftest"]:
        sys.exit(0 if selftest() else 1)
    print(__doc__)
