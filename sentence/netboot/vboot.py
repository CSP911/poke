#!/usr/bin/env python3
"""A virtual Pi 4 bootloader for the twin: the whole netboot path without the Pi.

It asks the hub's TFTP server the way the Pi 4's EEPROM bootloader set to
BOOT_ORDER=0xf2 does, in the order a real netboot shows (rpi-eeprom docs and
release notes; a published dnsmasq log of a Pi 4 netboot):

  bootloader   <serial>/start4.elf   prefix probe (tsize, then early terminate);
                                     answered → keep the serial prefix, "not found" → drop it
               config.txt            the bootloader reads it (start file, gpu_mem)
               recover4.elf, recovery.elf      expected "not found"
               start4.elf + fixup4.dat         or start4cd.elf + fixup4cd.dat when gpu_mem=16
  firmware     config.txt, bcm2711-rpi-4-b.dtb, overlays/overlay_map.dtb,
               cmdline.txt, armstub8-gic.bin   (missing ones are fine)
               <kernel> probe (tsize, early terminate), then <kernel> in full

Signed boot (--signed PUBKEY, the EEPROM's SIGNED_BOOT=1 with the hub's public key): after the probe it asks
only for boot.sig and boot.img, checks that boot.sig names this boot.img and carries an RSA-2048 signature by
that key (../seal.py, the check the Pi 4 bootloader makes), and only then opens the ramdisk and reads
config.txt, the firmware pair and the kernel from inside it. A refused image is never booted: the report says
why ("refused") and the bootloader goes back to asking. Plain files on the server are not asked for at all.
The booted image's runtime then asks the hub for a ticket on a fresh nonce (replay, ../seal.py stamp); here
the twin's UART wire carries that question to the hub's tftpd as poke-ticket.<nonce>.<image sha256> and
types the answer back. The report's "fresh" says what the hub answered.

Each request waits up to TFTP_FILE_TIMEOUT for an answer, like the real one; a
missing required file or a timeout fails this network boot, and after a short
network restart (RESTART, 0xf) it starts again from the probe. When the image
has arrived it boots the twin with it, writes netboot/vboot-out/<n>.json for
the hub, and goes back to asking — the node holds nothing of ours.

    vboot.py [--port 16969] [--host 127.0.0.1] [--file-timeout 30] [--restart-s 0.25] [--out DIR] [--once]
             [--signed PUBKEY]
"""
import os, sys, json, time, socket, struct
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import say, seal

OUT = os.path.join(HERE, "vboot-out")
SERIAL = "1a2b3c4d"
BLKSIZE = 1468                                     # the bootloader's TFTP_MAX_BLOCKSIZE since 2022-10
RRQ, DATA, ACK, ERROR, OACK = 1, 3, 4, 5, 6
NOT_FOUND, TIMEOUT = "not found", "timeout"

def log(*a):
    print(time.strftime("%H:%M:%S"), "[bootloader]", *a, flush=True)

def arg(name, default, cast=str):
    return cast(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default

def rrq(server, name, timeout, probe=False, why=None):
    """One TFTP read. Returns the bytes, NOT_FOUND, TIMEOUT, or (probe) the size from the OACK.
    why: a list that gets the server's error message."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(timeout)
    opts = b"tsize\0" + b"0\0" + (b"" if probe else b"blksize\0" + str(BLKSIZE).encode() + b"\0")
    s.sendto(struct.pack("!H", RRQ) + name.encode() + b"\0octet\0" + opts, server)
    out, blk, expect = bytearray(), 512, 1
    try:
        while True:
            pkt, tid = s.recvfrom(65536)
            op = struct.unpack("!H", pkt[:2])[0]
            if op == ERROR:
                if why is not None: why.append(pkt[4:].split(b"\0")[0].decode(errors="replace"))
                return NOT_FOUND
            if op == OACK:
                f = pkt[2:].split(b"\0"); o = {f[i].decode().lower(): f[i + 1].decode() for i in range(0, len(f) - 1, 2) if f[i]}
                if probe:                                  # existence/size check only: early terminate
                    s.sendto(struct.pack("!HH", ERROR, 0) + b"Early terminate\0", tid)
                    return int(o.get("tsize", 0))
                blk = int(o.get("blksize", 512)); s.sendto(struct.pack("!HH", ACK, 0), tid); s.settimeout(2.0); continue
            if op == DATA:
                n = struct.unpack("!H", pkt[2:4])[0]
                if probe:
                    s.sendto(struct.pack("!HH", ERROR, 0) + b"Early terminate\0", tid); return len(pkt) - 4
                if n == expect & 0xFFFF:
                    out += pkt[4:]; expect += 1
                s.sendto(struct.pack("!HH", ACK, n), tid); s.settimeout(2.0)
                if n == (expect - 1) & 0xFFFF and len(pkt) - 4 < blk:
                    return bytes(out)
    except (socket.timeout, OSError):                  # OSError: nothing listening yet (port unreachable)
        return TIMEOUT
    finally:
        s.close()

def cfg_value(cfg, key, default=None):
    v = default
    for l in cfg.splitlines():
        l = l.split("#", 1)[0].strip()
        if l.startswith(key + "="):
            v = l.split("=", 1)[1].strip()
    return v

class Refused(Exception):
    """The image arrived but the bootloader will not run it (signed boot)."""
    def __init__(self, why, rec):
        super().__init__(why); self.why, self.rec = why, rec

def netboot(server, file_timeout, pubkey=None):
    """One NETWORK boot-mode attempt. Returns (image, kernel name, record) or (None, reason, record).
    With pubkey (signed boot) a fetched but unsigned or tampered image raises Refused."""
    reqs = []
    def get(name, probe=False, timeout=None):
        t = time.monotonic()
        r = rrq(server, name, timeout if timeout is not None else file_timeout, probe)
        reqs.append({"file": name, "probe": probe, "got": r if isinstance(r, str) else (r if probe else len(r)),
                     "ms": round((time.monotonic() - t) * 1000, 1)})
        return r
    # bootloader: the prefix probe is the request an idle hub leaves waiting (parked)
    p = get(f"{SERIAL}/start4.elf", probe=True)
    if p == TIMEOUT:
        return None, "no answer", reqs
    t0 = time.monotonic()
    pre = f"{SERIAL}/" if p != NOT_FOUND else ""
    if pubkey:
        return signed_boot(get, pre, pubkey, reqs, t0)
    cfg = get(pre + "config.txt")
    if cfg == TIMEOUT:
        return None, "config.txt timed out", reqs
    cfg = cfg.decode(errors="replace") if isinstance(cfg, bytes) else ""
    get(pre + "recover4.elf"); get(pre + "recovery.elf")
    cd = cfg_value(cfg, "gpu_mem") == "16"
    start, fixup = ("start4cd.elf", "fixup4cd.dat") if cd else ("start4.elf", "fixup4.dat")
    for f in (start, fixup):
        if not isinstance(get(pre + f), bytes):
            return None, f"{f} missing", reqs
    # firmware (start4.elf) on the VideoCore
    get(pre + "config.txt")
    get(pre + "bcm2711-rpi-4-b.dtb"); get(pre + "overlays/overlay_map.dtb")
    get(pre + cfg_value(cfg, "cmdline", "cmdline.txt")); get(pre + "armstub8-gic.bin")
    kernel = cfg_value(cfg, "kernel", "kernel8.img")
    if not isinstance(get(pre + kernel, probe=True), int):
        return None, f"{kernel} missing", reqs
    img = get(pre + kernel)
    if not isinstance(img, bytes):
        return None, f"{kernel} failed", reqs
    return img, kernel, {"requests": reqs, "fetched_ms": round((time.monotonic() - t0) * 1000), "start_file": start}

def signed_boot(get, pre, pubkey, reqs, t0):
    """SIGNED_BOOT=1: boot.sig + boot.img only; verify before anything inside is read."""
    sig = get(pre + "boot.sig")
    if sig == TIMEOUT:
        return None, "boot.sig timed out", reqs
    img = get(pre + "boot.img")
    if img == TIMEOUT:
        return None, "boot.img timed out", reqs
    rec = {"requests": reqs, "fetched_ms": round((time.monotonic() - t0) * 1000), "signed": True}
    if not isinstance(img, bytes):
        if sig == NOT_FOUND:
            return None, "no boot.img / boot.sig", reqs        # nothing sealed is offered: like an idle hub
        raise Refused("no boot.img", rec)
    files, why = seal.open_boot(img, sig if isinstance(sig, bytes) else b"", pubkey)
    if files is None:
        raise Refused(why, rec)
    cfg = files.get("config.txt", b"").decode(errors="replace")
    cd = cfg_value(cfg, "gpu_mem") == "16"
    start, fixup = ("start4cd.elf", "fixup4cd.dat") if cd else ("start4.elf", "fixup4.dat")
    for f in (start, fixup):
        if f not in files:
            return None, f"{f} missing in boot.img", reqs
    kernel = cfg_value(cfg, "kernel", "kernel8.img")
    if kernel not in files:
        return None, f"{kernel} missing in boot.img", reqs
    try: manifest = json.loads(files.get("poke.json") or b"{}")
    except ValueError: manifest = {}
    return files[kernel], kernel, {**rec, "start_file": start, "boot_img_bytes": len(img), "manifest": manifest,
                                   "key": seal.fingerprint(pubkey)}

def ticket_wire(server):
    """The twin's UART to the hub: a POKE-NONCE becomes a ticket request to the hub's tftpd."""
    def ask(nonce, image_sha256):
        why = []
        r = rrq(server, f"poke-ticket.{nonce.hex()}.{image_sha256.hex()}", 5.0, why=why)
        if isinstance(r, bytes):
            return r, "ticket"
        return None, why[0] if why else r
    return ask

def main():
    server = (arg("--host", "127.0.0.1"), arg("--port", 16969, int))
    file_timeout = arg("--file-timeout", 30.0, float)          # TFTP_FILE_TIMEOUT (bootloader default 30 s)
    restart_s = arg("--restart-s", 0.25, float)                # network re-init between attempts
    out_dir = arg("--out", OUT)                                # where boot reports go (the hub reads them)
    pubkey = arg("--signed", None)                             # SIGNED_BOOT=1 with the hub's public key
    os.makedirs(out_dir, exist_ok=True)
    n = 1 + max([int(f[:-5]) for f in os.listdir(out_dir) if f.endswith(".json") and f[:-5].isdigit()] or [-1])
    idle = 0
    if pubkey:
        log(f"signed boot: only images signed by key {seal.fingerprint(pubkey)} boot")
    while True:
        try:
            img, what, rec = netboot(server, file_timeout, pubkey)
        except Refused as r:                                   # fetched, checked, not run: the hub hears why
            log(f"REFUSED boot.img: {r.why} — not booted; nothing of it runs")
            json.dump({"boot": n, **r.rec, "refused": r.why, "powered_off": False, "result": None},
                      open(os.path.join(out_dir, f"{n}.json"), "w"), indent=1, ensure_ascii=False)
            n += 1
            if "--once" in sys.argv:
                return
            time.sleep(restart_s); continue
        if img is None:
            idle += 1
            if what != "no answer" or idle % 20 == 1:
                log(f"network boot failed ({what}) — restarting; nothing of ours on the node")
            time.sleep(restart_s); continue
        idle = 0
        path = os.path.join(out_dir, f"boot-{n}.img"); open(path, "wb").write(img)
        log(f"fetched {rec['start_file']}, config.txt, {what} {len(img)} B in {rec['fetched_ms']} ms "
            f"({len(rec['requests'])} requests) → booting")
        bpath = os.path.join(HERE, "bench.json")
        bench = json.load(open(bpath)) if os.path.exists(bpath) else say.default_bench()
        b = say.boot(path, bench, ticket=ticket_wire(server) if pubkey else None)
        f = b.get("fresh") or {}
        if f.get("asked"):
            log(f"runtime asked the hub for a ticket: {'given' if f.get('ticket') else 'refused — ' + str(f.get('hub'))}")
        os.remove(path)                                        # the image is gone with the boot
        out = {"boot": n, **rec, **{k: v for k, v in b.items()}}
        json.dump(out, open(os.path.join(out_dir, f"{n}.json"), "w"), indent=1, ensure_ascii=False)
        log(f"node {'powered off' if b['powered_off'] else 'did NOT power off'} after {b['boot_to_off_ms']} ms → back to waiting")
        n += 1
        if "--once" in sys.argv:
            return

if __name__ == "__main__":
    main()
