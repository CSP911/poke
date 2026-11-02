#!/usr/bin/env python3
"""Sentence boot on x86 PCs: one sentence → one network boot (PXE) → the PC computes, reports, is
wiped and resets. The idle-PC side of POKE: an office PC at night that boots our image instead of its
disk, runs one job, leaves nothing, and next boot (no sentence pending) falls through to its own OS.

The twin is QEMU's PC (`-M pc`, e1000 NIC with its iPXE boot ROM). iPXE fetches a Multiboot ELF over
TFTP — from QEMU's built-in TFTP (default), or from the hub's tftpd, which answers only while a
sentence is pending (--netboot), exactly as for the Pi.

    x86.py "1부터 1000만까지 소수 개수 세줘"         op by Claude Code (cached), PXE boot, result
    x86.py --netboot "…"                            through the hub's tftpd (started and stopped here)
    x86.py --parts 4 "…"                            the job split across 4 PCs booting at once
    x86.py --station "문장1" "문장2" …               one PC booted once into iPXE; each sentence is a PXE program
                                                    that runs, zeroes what it used and returns (≈0.05 s, no reboot)
    x86.py selftest                                 the runtime's fences, no model calls
"""
import os, sys, re, json, time, shutil, hashlib, subprocess, tempfile, threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EDGE = os.path.join(ROOT, "edge", "sentence-x86")
BUILD = os.path.join(HERE, "build", "x86")
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "sim", "body"))
import say                                          # QMP, parse_result

CC, LD, NM = "i686-elf-gcc", "i686-elf-ld", "i686-elf-nm"
CFLAGS = ["-ffreestanding", "-nostdlib", "-fno-builtin", "-fno-stack-protector", "-fno-pic", "-O2", "-Wall",
          "-mgeneral-regs-only", "-fno-common", "-I", EDGE]

def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[-1500:])
    return r.stdout

# ── build ────────────────────────────────────────────────────────────────
def runtime_objs():
    d = os.path.join(BUILD, "rt"); os.makedirs(d, exist_ok=True)
    out = []
    for src, obj in (("boot.S", "poke_rt_boot.o"), ("rt.c", "poke_rt_rt.o"), ("ustub.S", "ustub.o")):
        o, s = os.path.join(d, obj), os.path.join(EDGE, src)
        if not os.path.exists(o) or os.path.getmtime(o) < max(os.path.getmtime(s), os.path.getmtime(os.path.join(EDGE, "rt.h"))):
            tmp = f"{o}.{os.getpid()}.{threading.get_ident()}"
            sh([CC, *CFLAGS, "-c", s, "-o", tmp]); os.replace(tmp, o)
        out.append(o)
    return out

def build(op_src, tag, defines=None):
    """op C source → ELF image path (Multiboot, what iPXE loads)."""
    os.makedirs(BUILD, exist_ok=True)
    work = tempfile.mkdtemp(prefix=".b-", dir=BUILD)
    try:
        c, o, elf = (os.path.join(work, f"op.{e}") for e in ("c", "o", "elf"))
        head = "".join(f"#define {k} {v}\n" for k, v in (defines or {}).items())
        open(c, "w").write(head + op_src)
        sh([CC, *CFLAGS, "-c", c, "-o", o])
        libgcc = sh([CC, "-print-libgcc-file-name"]).strip()
        sh([LD, "-T", os.path.join(EDGE, "image.ld"), "--no-warn-rwx-segments", "-o", elf, *runtime_objs(), o, libgcc])
        final = os.path.join(BUILD, f"{tag}.elf")
        os.replace(elf, final)
        open(os.path.join(BUILD, f"{tag}.c"), "w").write(head + op_src)
        return final
    finally:
        shutil.rmtree(work, ignore_errors=True)

def symbols(elf):
    out = {}
    for l in sh([NM, elf]).splitlines():
        f = l.split()
        if len(f) == 3: out[f[2]] = int(f[0], 16)
    return out

def build_nbp(op_src, tag, defines=None):
    """op C source → a returning PXE program (sentence-x86.pxe): nbp.S stub + the payload (runtime + op,
    linked at 1 MiB as for Multiboot). For a PC that stays in its network bootloader between sentences."""
    elf = build(op_src, tag, defines)
    work = tempfile.mkdtemp(prefix=".n-", dir=BUILD)
    try:
        blob = os.path.join(work, "payload.bin")
        sh(["i686-elf-objcopy", "-O", "binary", elf, blob])
        so, se, sb = (os.path.join(work, f"stub.{e}") for e in ("o", "elf", "bin"))
        sh([CC, "-c", os.path.join(EDGE, "nbp.S"), "-o", so])
        sh([LD, "-Ttext=0x7C00", "-e", "_start", "-o", se, so])
        sh(["i686-elf-objcopy", "-O", "binary", se, sb])
        stub, payload, st = bytearray(open(sb, "rb").read()), bytearray(open(blob, "rb").read()), symbols(se)
        mb = payload.find(b"\x02\xb0\xad\x1b")             # the Multiboot header: iPXE would take the file for a
        if 0 <= mb < 8192: payload[mb:mb + 12] = bytes(12)   # Multiboot image and refuse it — a PXE program has none
        off = (len(stub) + 15) & ~15
        entry = symbols(elf)["_start_nbp"]
        if 0x7C00 + off + len(payload) > 0x9F000:
            raise RuntimeError(f"PXE program too large for conventional memory ({off + len(payload)} B)")
        import struct
        for name, val in (("payload_off", off), ("payload_len", len(payload)), ("payload_entry", entry)):
            struct.pack_into("<I", stub, st[name] - 0x7C00, val)
        out = os.path.join(BUILD, f"{tag}.pxe")
        open(out + ".tmp", "wb").write(bytes(stub) + bytes(off - len(stub)) + payload)
        os.replace(out + ".tmp", out)
        return out, elf, {"stub": len(stub), "payload_off": off, "payload": len(payload)}
    finally:
        shutil.rmtree(work, ignore_errors=True)

# ── a PC that stays in its network bootloader (iPXE) between sentences ────
class Station:
    """One twin PC, network-booted once into iPXE, looping on the hub's tftpd (which answers only while
    a sentence is pending). Each sentence: the hub makes it pending → the parked request is answered →
    the PC runs it as a PXE program, which zeroes what it used and returns to iPXE. No reset, no POST,
    no DHCP between sentences."""
    def __init__(self):
        self.tport = 30000 + int.from_bytes(os.urandom(2), "little") % 20000
        self.qport = 20000 + int.from_bytes(os.urandom(2), "little") % 9000
        self.root = tempfile.mkdtemp(prefix=".st-", dir=BUILD)
        self.tftp = tempfile.mkdtemp(prefix=".sc-", dir=BUILD)
        open(os.path.join(self.tftp, "loop.ipxe"), "w").write(
            "#!ipxe\n:loop\nchain tftp://10.0.2.2:%d/sentence-x86.pxe || goto loop\ngoto loop\n" % self.tport)
        self.srv = subprocess.Popen([sys.executable, os.path.join(HERE, "netboot", "tftpd.py"), "--port", str(self.tport), "--root", self.root],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.makedirs(os.path.join(BUILD, "serial"), exist_ok=True)
        self.serial = os.path.join(BUILD, "serial", f"st-{os.getpid()}-{self.qport}.txt"); open(self.serial, "w").close()
        t0 = time.time()
        self.q = subprocess.Popen(["qemu-system-x86_64", "-M", "pc", "-m", "64", "-display", "none", "-serial", f"file:{self.serial}",
                                   "-netdev", f"user,id=n0,tftp={self.tftp},bootfile=loop.ipxe", "-device", "e1000,netdev=n0", "-boot", "n",
                                   "-qmp", f"tcp:127.0.0.1:{self.qport},server=on,wait=off", "-monitor", "none"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.mon = say.QMP(self.qport)
        self.started = t0
        self.seen = 0

    def say(self, pxe, elf, budget_s=40):
        """Make one image pending; wait for the PC to run it and come back. → result dict."""
        t0 = time.time()
        subprocess.run([sys.executable, os.path.join(HERE, "netboot", "tftpd.py"), "--pend", pxe, "--root", self.root],
                       check=True, capture_output=True)
        t_boot = t_off = None
        while time.time() - t0 < budget_s:
            txt = open(self.serial, errors="replace").read().replace("\r", "")
            new = txt[self.seen:]
            if t_boot is None and "POKE-BOOT" in new: t_boot = time.time()
            if "POKE-OFF" in new: t_off = time.time(); break
            time.sleep(0.003)
        wipe = None
        if t_off:
            self.mon.call("stop")                         # read back the memory before iPXE loads anything else
            wipe = self.wipe(pxe, elf)
            self.mon.call("cont")
        lines = new.splitlines()
        self.seen = len(txt)
        res, bad = say.parse_result(lines)
        end = next((l for l in lines if l.startswith("POKE-END")), "")
        m = re.search(r"ms=(\d+)", end)
        return {"lines": lines, "result": res, "result_error": bad, "powered_off": bool(t_off), "returned": bool(t_off),
                "node_ms": int(m.group(1)) if m else None, "wipe_check": wipe,
                "netboot_s": round(t_boot - t0, 3) if t_boot else None, "power_on_to_off_s": round((t_off or time.time()) - t0, 3)}

    def wipe(self, pxe, elf):
        """Two places held the run: the payload at 1 MiB ([__rt_text_end, __rt_end)) and its copy loaded
        after the stub at 0x7C00. Both must read back as zero."""
        s = symbols(elf); a, b = s["__rt_text_end"], s["__rt_end"]
        hdr = open(pxe, "rb").read(16)                      # nbp.S: jmp, then payload_off, payload_len at 4 and 8
        lo_from, lo_len = 0x7C00 + int.from_bytes(hdr[4:8], "little"), int.from_bytes(hdr[8:12], "little")
        tot, nz = 0, 0
        for start, size in ((a, b - a), (lo_from, lo_len)):
            dump = os.path.join(BUILD, "serial", f"wipe-{os.getpid()}-{os.urandom(4).hex()}.bin")
            try:
                self.mon.call("pmemsave", val=start, size=size, filename=dump)
                raw = open(dump, "rb").read()
            finally:
                if os.path.exists(dump): os.remove(dump)
            tot += len(raw); nz += sum(1 for x in raw if x)
        return {"bytes": tot, "nonzero": nz}

    def close(self):
        try: self.mon.call("quit")
        except (OSError, ValueError, RuntimeError): pass
        try: self.q.wait(5)
        except subprocess.TimeoutExpired: self.q.kill()
        self.srv.terminate()
        try: self.srv.wait(3)
        except subprocess.TimeoutExpired: self.srv.kill()
        for d in (self.root, self.tftp): shutil.rmtree(d, ignore_errors=True)
        try: os.remove(self.serial)
        except OSError: pass

# ── boot one PC (twin) ────────────────────────────────────────────────────
def boot(elf, budget_s=60, netboot_port=None):
    """PXE-boot the twin PC with this image. netboot_port: fetch from the hub's tftpd at that port
    (the image must be pending there); otherwise QEMU's built-in TFTP serves a private copy."""
    port = 20000 + int.from_bytes(os.urandom(2), "little") % 20000
    sdir = os.path.join(BUILD, "serial"); os.makedirs(sdir, exist_ok=True)
    serial = os.path.join(sdir, f"{os.getpid()}-{port}.txt"); open(serial, "w").close()
    tdir = tempfile.mkdtemp(prefix=".tftp-", dir=BUILD)
    if netboot_port:
        net = f"user,id=n0,bootfile=tftp://10.0.2.2:{netboot_port}/sentence-x86.elf"
    else:
        shutil.copy(elf, os.path.join(tdir, "sentence-x86.elf"))
        net = f"user,id=n0,tftp={tdir},bootfile=sentence-x86.elf"
    t0 = time.time()
    q = subprocess.Popen(["qemu-system-x86_64", "-M", "pc", "-m", "64", "-display", "none", "-S",
                          "-serial", f"file:{serial}", "-netdev", net, "-device", "e1000,netdev=n0", "-boot", "n",
                          "-qmp", f"tcp:127.0.0.1:{port},server=on,wait=off", "-monitor", "none",
                          "-action", "reboot=shutdown,shutdown=pause"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    mon, off_line, vm_off, status, wipe, t_boot, t_off = None, False, False, "?", None, None, None
    try:
        mon = say.QMP(port)
        mon.call("cont")
        while time.time() - t0 < budget_s:
            txt = open(serial, errors="replace").read()
            if t_boot is None and "POKE-BOOT" in txt: t_boot = time.time()
            if "POKE-OFF" in txt: off_line = True; break
            if q.poll() is not None: break
            time.sleep(0.05)
        t_off = time.time()
        for _ in range(50):
            st = mon.call("query-status")
            vm_off = st.get("status") == "shutdown" or not st.get("running")
            if vm_off or not off_line: break
            time.sleep(0.02)
        status = st.get("status")
        if vm_off:
            wipe = wipe_check(mon, elf)
    finally:
        if mon:
            try: mon.call("quit")
            except (OSError, ValueError, RuntimeError): pass
        try: q.wait(5)
        except subprocess.TimeoutExpired: q.kill(); q.wait()
        try: lines = open(serial, errors="replace").read().replace("\r", "").splitlines()
        except OSError: lines = []
        for p in (serial,):
            try: os.remove(p)
            except OSError: pass
        shutil.rmtree(tdir, ignore_errors=True)
    res, bad = say.parse_result(lines)
    end = next((l for l in lines if l.startswith("POKE-END")), "")
    m = re.search(r"ms=(\d+)", end)
    return {"lines": lines, "result": res, "result_error": bad, "powered_off": off_line and vm_off, "vm_status": status,
            "node_ms": int(m.group(1)) if m else None, "wipe_check": wipe,
            "netboot_s": round(t_boot - t0, 2) if t_boot else None,          # power-on → our code running (PXE)
            "power_on_to_off_s": round((t_off or time.time()) - t0, 2)}

def wipe_check(mon, elf):
    """After the PC reset itself: read back what the image used beyond its code — op pages, runtime
    data, stacks [__rt_text_end, __rt_end) — and count non-zero bytes. Evidence, not the node's claim."""
    s = symbols(elf)
    a, b = s.get("__rt_text_end"), s.get("__rt_end")
    if not a or not b or b <= a: return None
    dump = os.path.join(BUILD, "serial", f"wipe-{os.getpid()}-{os.urandom(4).hex()}.bin")
    try:
        mon.call("pmemsave", val=a, size=b - a, filename=dump)
        raw = open(dump, "rb").read()
    except (OSError, RuntimeError):
        return None
    finally:
        if os.path.exists(dump): os.remove(dump)
    return {"from": a, "to": b, "bytes": len(raw), "nonzero": sum(1 for x in raw if x)} if len(raw) == b - a else None

# ── through the hub's tftpd (answers only while a sentence is pending) ────
def boot_netboot(elf, budget_s=60):
    port = 30000 + int.from_bytes(os.urandom(2), "little") % 20000
    root = tempfile.mkdtemp(prefix=".nb-", dir=BUILD)
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "netboot", "tftpd.py"), "--port", str(port), "--root", root],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        time.sleep(0.4)
        subprocess.run([sys.executable, os.path.join(HERE, "netboot", "tftpd.py"), "--pend", elf, "--root", root],
                       check=True, capture_output=True)
        b = boot(elf, budget_s, netboot_port=port)
        b["consumed"] = not os.path.exists(os.path.join(root, "pending.img"))
        return b
    finally:
        srv.terminate()
        try: srv.wait(3)
        except subprocess.TimeoutExpired: srv.kill()
        shutil.rmtree(root, ignore_errors=True)

# ── the sentence → op (compute; no devices on a PC) ───────────────────────
OP_SYSTEM = """You write the op for ONE sentence on POKE, run on an office PC that boots over the network with no operating system, computes, reports, and is wiped. Output ONE C file and nothing else — no markdown fences.

The op runs in 32-bit x86 ring 3 with no OS, no libc, no floating point (compiled with -mgeneral-regs-only: do not use float or double at all). `long` is 32 bits; use `long long` (64-bit) when values can exceed 2^31 — 64-bit division works. It may only call the runtime:
#include "rt.h"
  void say(const char *s);                       // one line of narration, Korean is fine
  void report_int(const char *key, long v);       // result field (32-bit)
  void report_fixed(const char *key, long v, int decimals);   // v / 10^decimals
  void report_str(const char *key, const char *s);
  void fail(const char *why);                    // then return
  unsigned long ms(void);                        // ms since boot
  void delay_ms(unsigned long n);                // n <= 10000
Define `void op_main(void)`. No other functions from outside; write any helper yourself (static). Memory: globals and stack only (stack 16 KB — put large arrays as static globals; keep total data under 2 MB). The whole run must finish within 20 s on one ordinary PC core; size the work to fit, or say why not with fail().
For a 64-bit result, report it with report_str after converting to decimal yourself.
If P_PART and P_PARTS are defined (the job is split across P_PARTS PCs and this is part P_PART, 0-based), do only this part's share. Report the values the hub must add up across parts as integers whose key starts with "sum_" (e.g. "sum_primes"), the same keys in every part; anything else (ranges, timings) under other keys; plus report_int("part", P_PART). Use #ifndef P_PART to default to 0 and 1.
Report with short snake_case keys; say() one sentence about what you did."""

def make_op(sentence, rounds=3):
    os.makedirs(os.path.join(BUILD, "ops"), exist_ok=True)
    key = hashlib.sha1(("x86\n" + sentence).encode()).hexdigest()[:12]
    cache = os.path.join(BUILD, "ops", f"{key}.c")
    if os.path.exists(cache):
        return {"src": open(cache).read(), "key": key, "cached": True, "gen_s": 0, "rounds": 0}
    import claude_cli
    prompt, t0 = f"Sentence: {sentence}", time.time()
    for r in range(1, rounds + 1):
        src = claude_cli.ask(OP_SYSTEM, prompt).strip()
        src = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", src)
        try:
            build(src, f"chk-{key}")
            open(cache, "w").write(src)
            return {"src": src, "key": key, "cached": False, "gen_s": round(time.time() - t0, 1), "rounds": r}
        except RuntimeError as e:
            prompt = f"Sentence: {sentence}\n\nYour previous op did not build:\n{str(e)[-1200:]}\n\nPrevious op:\n{src}\n\nWrite the corrected op."
    raise RuntimeError("Claude Code could not write an op that builds")

# ── run ──────────────────────────────────────────────────────────────────
def show(b, label="PC"):
    for l in b["lines"]:
        if l.startswith(("POKE-SAY", "POKE-FAULT")): print(f"   [{label}] {l}")
    w = b.get("wipe_check")
    wipe = f"리셋 뒤 실측: {w['bytes'] // 1024} KB 중 0이 아닌 바이트 {w['nonzero']}" if w else "실측 없음"
    print(f"[{label}] 결과 {json.dumps(b['result'], ensure_ascii=False)}")
    print(f"[{label}] {'꺼짐(리셋)' if b['powered_off'] else '꺼지지 않음'} · PXE 부팅 {b['netboot_s']} s · 계산 {b['node_ms']} ms · "
          f"전원~꺼짐 {b['power_on_to_off_s']} s · {wipe}")

def run(sentence, netboot=False):
    print(f"\n🗣  \"{sentence}\"  (x86 PC, 네트워크 부팅)")
    op = make_op(sentence)
    print(f"[허브] op {'기억에서' if op['cached'] else f'Claude Code가 {op['gen_s']} s에 작성'}")
    elf = build(op["src"], f"s-{op['key']}")
    print(f"[허브] 이미지 {os.path.getsize(elf)} B → {'허브 TFTP(문장 있을 때만 응답)' if netboot else 'PXE'}")
    b = boot_netboot(elf) if netboot else boot(elf)
    show(b)
    if netboot: print(f"[허브] TFTP: 문장 {'소비됨 — 다음 부팅엔 응답 없음(PC는 자기 디스크로)' if b.get('consumed') else '아직 남음'}")
    return b

def run_station(sentences):
    """One PC, network-booted once; then each sentence costs a TFTP fetch and the run, not a boot."""
    print(f"\n[허브] PC 한 대를 한 번만 네트워크 부팅해 iPXE에 머물게 합니다 (문장 {len(sentences)}개)")
    t0 = time.time(); st = Station()
    try:
        while "iPXE" not in open(st.serial, errors="replace").read() and time.time() - t0 < 3: time.sleep(0.05)
        time.sleep(7)                                  # first boot: BIOS, iPXE, DHCP (paid once)
        print(f"[허브] 첫 부팅 {round(time.time() - t0, 1)} s — 이후 문장마다 부팅 없음")
        for s in sentences:
            print(f"\n🗣  \"{s}\"")
            op = make_op(s)
            pxe, elf, info = build_nbp(op["src"], f"n-{op['key']}")
            print(f"[허브] op {'기억에서' if op['cached'] else f'Claude Code가 {op['gen_s']} s에 작성'} · PXE 프로그램 {os.path.getsize(pxe)} B")
            b = st.say(pxe, elf)
            for l in b["lines"]:
                if l.startswith(("POKE-SAY", "POKE-FAULT")): print(f"   [PC] {l}")
            w = b.get("wipe_check")
            print(f"[PC] 결과 {json.dumps(b['result'], ensure_ascii=False)}")
            print(f"[PC] 문장 → 실행 {b['netboot_s']} s · 계산 {b['node_ms']} ms · iPXE로 복귀 {b['power_on_to_off_s']} s · "
                  + (f"복귀 뒤 실측: {w['bytes'] // 1024} KB 중 0이 아닌 바이트 {w['nonzero']}" if w else "실측 없음"))
    finally:
        st.close()

def run_parts(sentence, n):
    print(f"\n🗣  \"{sentence}\"  (x86 PC {n}대가 나눠서)")
    op = make_op(sentence)
    print(f"[허브] op {'기억에서' if op['cached'] else f'Claude Code가 {op['gen_s']} s에 작성'} · 이미지 {n}개(부분 번호만 다름)")
    elves = [build(op["src"], f"p-{op['key']}-{i}", {"P_PART": i, "P_PARTS": n}) for i in range(n)]
    t0, outs = time.time(), [None] * n
    def one(i): outs[i] = boot(elves[i])
    ts = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in ts: t.start()
    for t in ts: t.join()
    total = {}
    for i, b in enumerate(outs):
        show(b, f"PC{i}")
        for k, v in (b["result"] or {}).items():
            if k.startswith("sum_") and isinstance(v, int) and not isinstance(v, bool):   # the op marks what adds up
                total[k[4:]] = total.get(k[4:], 0) + v
    print(f"[허브] 합친 결과 {json.dumps(total, ensure_ascii=False)} · {n}대 전체 {round(time.time() - t0, 2)} s")
    return outs, total

# ── selftest: the fences, no model ─────────────────────────────────────────
SELF = [
    ("primes", 'void op_main(void){ long n=0; for(long i=2;i<200000;i++){int p=1; for(long d=2;d*d<=i;d++) if(i%d==0){p=0;break;} n+=p;} report_int("primes",n); }',
     lambda b: b["result"] == {"primes": 17984}),
    ("port", 'void op_main(void){ __asm__ volatile("outb %0,%1"::"a"((unsigned char)90),"Nd"((unsigned short)0x3F8)); report_int("reached",1); }',
     lambda b: "protection" in json.dumps(b["result"])),
    ("runtime-memory", 'void op_main(void){ volatile unsigned *p=(unsigned*)0x100100; report_int("w",(long)*p); }',
     lambda b: "outside its pages" in json.dumps(b["result"])),
    ("null", 'void op_main(void){ volatile int *p=0; report_int("x",*p); }', lambda b: "outside its pages" in json.dumps(b["result"])),
    ("stack", 'static int f(int n){ volatile char b[256]; b[0]=(char)n; return f(n+1)+b[0]; }\nvoid op_main(void){ report_int("r",f(0)); }',
     lambda b: "stack" in json.dumps(b["result"])),
    ("bad-pointer", 'void op_main(void){ say((const char*)0x100000); }', lambda b: "pointer" in json.dumps(b["result"])),
    ("big-result", 'void op_main(void){ char k[8]={\'k\',0,0,0}; for(int i=0;i<200;i++){ k[1]=(char)(\'a\'+i%26); k[2]=(char)(\'a\'+i/26); report_int(k,i);} }',
     lambda b: (b["result"] or {}).get("truncated") is True),
]

def selftest():
    ok = 0
    for name, body, want in SELF:
        elf = build('#include "rt.h"\n' + body, f"self-{name}")
        b = boot(elf)
        good = b["powered_off"] and want(b) and (b["wipe_check"] or {}).get("nonzero") == 0
        ok += good
        w = b.get("wipe_check") or {}
        print(f"{'✓' if good else '✗'} {name:<15} off={b['powered_off']} wipe={w.get('nonzero')}/{w.get('bytes')} "
              f"pxe={b['netboot_s']}s result={json.dumps(b['result'], ensure_ascii=False)[:90]}")
    # resident: one PC stays in iPXE; three PXE programs in a row (normal, faulting, normal) — each must
    # start within a second of becoming pending, return without a reboot, and leave both copies zeroed
    st, rok, rn = Station(), 0, 0
    try:
        time.sleep(9)
        for name, body, want in (SELF[0], SELF[3], SELF[0]):
            rn += 1
            pxe, elf, _ = build_nbp('#include "rt.h"\n' + body, f"self-nbp-{rn}")
            b = st.say(pxe, elf, budget_s=20)
            good = b["returned"] and want(b) and (b["wipe_check"] or {}).get("nonzero") == 0 and (b["netboot_s"] or 99) < 1.0
            rok += good
            print(f"{'✓' if good else '✗'} resident-{name:<6} start={b['netboot_s']}s back={b['power_on_to_off_s']}s "
                  f"wipe={(b['wipe_check'] or {}).get('nonzero')}/{(b['wipe_check'] or {}).get('bytes')} result={json.dumps(b['result'], ensure_ascii=False)[:60]}")
    finally:
        st.close()
    print(f"\nx86 selftest {ok + rok}/{len(SELF) + rn}")
    return ok == len(SELF) and rok == rn

if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "selftest":
        sys.exit(0 if selftest() else 1)
    nb = "--netboot" in a; a = [x for x in a if x != "--netboot"]
    if a and a[0] == "--station":
        run_station(a[1:])
    elif "--parts" in a:
        i = a.index("--parts"); n = int(a[i + 1]); del a[i:i + 2]
        run_parts(" ".join(a), n)
    elif a:
        run(" ".join(a), netboot=nb)
    else:
        print(__doc__)
