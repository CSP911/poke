#!/usr/bin/env python3
"""Hardware-team revision of sentence/netboot/tftpd.py (proposal; the original is untouched).

Changes against the original, each for a real-Pi failure the twin cannot show:

1. Parked requests. While idle, a read request is not answered but remembered
   (newest per Pi, for PARK_S seconds). When a sentence becomes pending, the
   parked request is answered at once. The Pi's bootloader waits up to
   TFTP_FILE_TIMEOUT (default 30 s) for an answer to a request; whether it
   retransmits the request inside that window is not documented. Answering the
   parked request makes "pending → firmware starts loading" independent of
   that, instead of costing up to one timeout plus a network restart.
2. Duplicate requests. A retransmitted request for a transfer already running
   (same client, port and file) is dropped instead of starting a second,
   parallel transfer of the same file.
3. Timing log. Every request and completed transfer is appended to
   ROOT/../netboot-times.jsonl with a monotonic timestamp, so the first real
   netboot gives its time budget per file (bootloader, start4.elf, fixup,
   config, dtb, image) without a packet capture.

Usage is the same:  tftpd.py [--port 69] [--root DIR]   |   tftpd.py --pend IMAGE
"""
import os, sys, time, json, socket, struct, threading

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "netboot", "root")
PENDING = "pending.img"
SERVED_AS = {"kernel8.img", "sentence.img"}
FIRMWARE = {"start4.elf", "fixup4.dat", "start4cd.elf", "fixup4cd.dat", "config.txt", "bcm2711-rpi-4-b.dtb", "cmdline.txt"}
RRQ, DATA, ACK, ERROR, OACK = 1, 3, 4, 5, 6
PARK_S = 25.0                       # < the bootloader's TFTP_FILE_TIMEOUT (default 30 s, minimum 5 s)

T0 = time.monotonic()
lock = threading.Lock()
parked = {}                         # client ip -> (pkt, (ip, port), t)
active = set()                      # (ip, port, name) transfers in progress

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

def tlog(root, **rec):
    rec["t"] = round(time.monotonic() - T0, 4)
    try:
        with open(os.path.join(root, "..", "netboot-times.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass

def pending_path(root):
    return os.path.join(root, PENDING)

def is_pending(root):
    return os.path.exists(pending_path(root))

def send_file(client, data, opts):
    blk = 512
    if "blksize" in opts:
        blk = max(8, min(int(opts["blksize"]), 1468))
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(1)
    if opts:
        o = b""
        if "blksize" in opts: o += b"blksize\0" + str(blk).encode() + b"\0"
        if "tsize" in opts: o += b"tsize\0" + str(len(data)).encode() + b"\0"
        for _ in range(5):
            s.sendto(struct.pack("!H", OACK) + o, client)
            try:
                pkt, _ = s.recvfrom(1500)
                if struct.unpack("!HH", pkt[:4]) == (ACK, 0): break
                if struct.unpack("!H", pkt[:2])[0] == ERROR: return False, blk
            except socket.timeout:
                continue
        else:
            return False, blk
    n = 1
    while True:
        chunk = data[(n - 1) * blk: n * blk]
        for _ in range(8):
            s.sendto(struct.pack("!HH", DATA, n & 0xFFFF) + chunk, client)
            try:
                while True:                              # drain duplicate ACKs of earlier blocks
                    pkt, _ = s.recvfrom(1500)
                    op, num = struct.unpack("!HH", pkt[:4])
                    if op == ERROR: return False, blk
                    if op == ACK and num == (n & 0xFFFF): break
                break
            except socket.timeout:
                continue
        else:
            return False, blk
        if len(chunk) < blk:
            return True, blk
        n += 1

def handle(pkt, client, root):
    if struct.unpack("!H", pkt[:2])[0] != RRQ:
        return
    parts = pkt[2:].split(b"\0")
    name = os.path.basename(parts[0].decode(errors="replace"))
    opts = {parts[i].decode().lower(): parts[i + 1].decode() for i in range(2, len(parts) - 1, 2) if parts[i]}
    tlog(root, ev="rrq", file=parts[0].decode(errors="replace"), opts=opts, client=list(client))
    if not is_pending(root):
        with lock:
            parked[client[0]] = (pkt, client, time.monotonic())
        log(f"idle: parked {name} from {client[0]}:{client[1]} (answered if a sentence arrives within {PARK_S:.0f} s)")
        return
    key = (client[0], client[1], name)
    with lock:
        if key in active:
            return                                       # a retransmission of a running transfer
        active.add(key)
    try:
        if name in SERVED_AS:
            path, consume = pending_path(root), True
        elif name in FIRMWARE or os.path.exists(os.path.join(root, name)):
            path, consume = os.path.join(root, name), False
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.sendto(struct.pack("!HH", ERROR, 1) + b"not found\0", client)
            log(f"not found: {name}"); tlog(root, ev="notfound", file=name); return
        try:
            data = open(path, "rb").read()
        except FileNotFoundError:                        # consumed by a parallel transfer
            return
        t = time.monotonic()
        ok, blk = send_file(client, data, opts)
        dt = time.monotonic() - t
        tlog(root, ev="sent" if ok else "aborted", file=name, bytes=len(data), blksize=blk, ms=round(dt * 1000, 1))
        log(f"{'sent' if ok else 'gave up on'} {name} ({len(data)} B, blk {blk}, {dt*1000:.0f} ms) to {client[0]}"
            + (" — the sentence's image" if consume else ""))
        if ok and consume:
            os.replace(path, path + ".booted")
            log("sentence consumed; back to idle")
    finally:
        with lock:
            active.discard(key)

def watch_pending(root):
    """When a sentence appears, answer the newest parked request of each Pi at once."""
    while True:
        time.sleep(0.02)
        if not is_pending(root):
            continue
        with lock:
            now = time.monotonic()
            todo = [(p, c) for (p, c, t) in parked.values() if now - t < PARK_S]
            parked.clear()
        for p, c in todo:
            log(f"sentence pending: answering the parked request from {c[0]}:{c[1]}")
            threading.Thread(target=handle, args=(p, c, root), daemon=True).start()

def serve(port, root):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    log(f"tftpd (hw) on :{port}, root {os.path.abspath(root)}, {'a sentence is pending' if is_pending(root) else 'idle'}")
    threading.Thread(target=watch_pending, args=(root,), daemon=True).start()
    while True:
        pkt, client = s.recvfrom(1500)
        threading.Thread(target=handle, args=(pkt, client, root), daemon=True).start()

if __name__ == "__main__":
    a = sys.argv[1:]
    root = a[a.index("--root") + 1] if "--root" in a else ROOT
    if "--pend" in a:
        img = a[a.index("--pend") + 1]
        os.makedirs(root, exist_ok=True)
        tmp = pending_path(root) + ".tmp"; open(tmp, "wb").write(open(img, "rb").read()); os.replace(tmp, pending_path(root))
        log(f"pending: {img} ({os.path.getsize(img)} B)")
    else:
        serve(int(a[a.index("--port") + 1]) if "--port" in a else 69, root)
