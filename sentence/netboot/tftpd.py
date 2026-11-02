#!/usr/bin/env python3
"""The hub's TFTP server for sentence boots on a real Pi 4.

The Pi's EEPROM bootloader (the manufacturer's firmware, not ours) fetches its
boot files over TFTP. This server answers only while a sentence is pending:

  idle      → a request is not answered, but parked (the newest per Pi, for
              PARK_S seconds). The node sits in its bootloader, waiting on that
              request for up to its TFTP_FILE_TIMEOUT (30 s by default), then
              restarts its network boot and asks again. None of our code is on it.
  pending   → the parked request is answered at once, then the firmware files
              and the sentence image are served; the node boots the sentence,
              reports and resets back into its bootloader. The pending sentence
              is consumed once the image has been sent in full (an aborted probe
              of the image — the firmware's "early terminate" — does not count).

Why parked: whether the bootloader re-sends a request inside its 30 s wait is
not documented. Answering the request it is already waiting on makes
"sentence pending → firmware loading" immediate either way.

Every request and transfer is appended to netboot/netboot-times.jsonl
(monotonic seconds), so a real netboot gives its time per file.

    tftpd.py [--port 69] [--root DIR]        serve (root holds the firmware files)
    tftpd.py --pend IMAGE [--target pi]       make IMAGE the next sentence (writes ROOT/pending.img);
                                              --target pi writes the Pi's RCFG (watchdog, I2C clock) into it

Signed boot: --pend also seals the final image (../seal.py) as pending.img.bootimg / .bootsig, served as
boot.img / boot.sig. A bootloader with SIGNED_BOOT=1 asks only for those and boots nothing that the hub's key
did not sign; one without it keeps asking for the plain files above. Either transfer consumes the sentence.

Tickets (replay): a signature says who made an image, not when, so a sealed image's runtime asks the hub
before its op runs (edge/sentence/rt_fresh.h): POKE-NONCE <nonce> <image sha256>. --pend writes what the
hub means to run now (ROOT/intent.json); the desk below signs the nonce only for that image, only after
boot.img went out, within TICKET_S, and only once — a replayed image, the hub's or a recorded one, gets
no ticket and runs nothing. A Pi asks over its UART (say.boot_pi answers with desk()); the twin's wire
(netboot/vboot.py) asks this server for the file poke-ticket.<nonce>.<image sha256>.

RFC 1350 read requests with the blksize/tsize options (RFC 2347/2348); block
numbers roll over. Path prefixes (serial-number directories) are ignored.
"""
import os, sys, time, json, socket, struct, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import seal                                         # ../seal.py: the hub's key, boot.img, tickets

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "root")
PENDING = "pending.img"
INTENT = "intent.json"                              # the image the hub means to run now: one ticket for it
TICKET_S = 120.0                                    # boot.img sent → ticket asked: firmware + boot take seconds
SEALED = {"boot.img": ".bootimg", "boot.sig": ".bootsig"}      # signed boot (seal.py): the ramdisk and its signature
SERVED_AS = {"kernel8.img", "sentence.img", "sentence-x86.elf", "sentence-x86.pxe"}   # Pi bootloader names; an x86 PC asks iPXE for sentence-x86.elf
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

def consume(root):
    """The sentence went out: every form of it (plain image, boot.img, boot.sig) is done."""
    p = pending_path(root)
    for suf in ("", *SEALED.values()):
        try: os.replace(p + suf, p + suf + ".booted")
        except OSError: pass

def _write(path, obj):
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    open(tmp, "w").write(json.dumps(obj)); os.replace(tmp, path)

def intend(root, boot_img):
    """--pend: this sealed image is what the hub means to run next (and nothing before it any more)."""
    m = json.loads(seal.unpack_fat(boot_img)["poke.json"])
    _write(os.path.join(root, INTENT), {"kernel_sha256": m["kernel_sha256"], "t_pend": time.time(), "t_served": None})

def served(root, boot_img):
    """boot.img went out: if it is the intended image, its ticket window opens now."""
    p = os.path.join(root, INTENT)
    try:
        it = json.load(open(p))
        h = json.loads(seal.unpack_fat(boot_img)["poke.json"])["kernel_sha256"]
    except (OSError, ValueError, KeyError, struct.error):
        return
    if it.get("kernel_sha256") == h and not it.get("t_served") and os.path.exists(p):
        _write(p, {**it, "t_served": time.time()})

def desk(root, nonce, image_sha256):
    """The hub's answer to a node's POKE-NONCE → (ticket line, "ticket") or (None, why). One ticket per intent."""
    if len(nonce) != 16 or len(image_sha256) != 32:
        return None, "malformed question"
    p, h = os.path.join(root, INTENT), image_sha256.hex()
    try:
        it = json.load(open(p))
    except (OSError, ValueError):
        try: done = json.load(open(p + ".ticketed")).get("kernel_sha256") == h
        except (OSError, ValueError): done = False
        return None, ("this image already had its ticket — a replay" if done else "the hub means to run nothing now")
    if it.get("kernel_sha256") != h:
        return None, "not the image the hub means to run now"
    if not it.get("t_served"):
        return None, "the hub has not sent this image yet"
    if time.time() - it["t_served"] > TICKET_S:
        return None, f"sent more than {TICKET_S:.0f} s ago"
    claim = f"{p}.{os.getpid()}.{threading.get_ident()}.claim"
    try: os.rename(p, claim)                          # the one ticket: whoever renames first
    except FileNotFoundError: return None, "this image already had its ticket — a replay"
    if json.load(open(claim)).get("kernel_sha256") != h:          # a new --pend landed in between: put it back
        try: os.link(claim, p)
        except FileExistsError: pass
        os.remove(claim)
        return None, "not the image the hub means to run now"
    os.replace(claim, p + ".ticketed")
    return seal.ticket(nonce, image_sha256), "ticket"

def answer_ticket(name, client, opts, root):
    """The twin's wire asks for poke-ticket.<nonce>.<image sha256>: the ticket line, or an error saying why."""
    try:
        _, n, h = name.split(".")
        line, why = desk(root, bytes.fromhex(n), bytes.fromhex(h))
    except ValueError:
        line, why = None, "malformed question"
    tlog(root, ev="ticket" if line else "noticket", why=why)
    log(f"ticket for {name.split('.')[-1][:12]}…: {'given' if line else 'refused — ' + why}")
    if line:
        send_file(client, line, opts)
    else:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(struct.pack("!HH", ERROR, 2) + why.encode()[:200] + b"\0", client)

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
            except OSError:                              # port unreachable: that request is gone
                return False, blk
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
            except OSError:
                return False, blk
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
    if name.startswith("poke-ticket."):
        return answer_ticket(name, client, opts, root)
    if not is_pending(root):
        with lock:
            parked[client[0]] = (pkt, client, time.monotonic())
        log(f"idle: ignoring {name} from {client[0]} for now (parked; answered if a sentence arrives within {PARK_S:.0f} s)")
        return
    key = (client[0], client[1], name)
    with lock:
        if key in active:
            return                                       # a retransmission of a running transfer
        active.add(key)
    try:
        if name in SERVED_AS:
            path, last = pending_path(root), True
        elif name in SEALED:
            path, last = pending_path(root) + SEALED[name], name == "boot.img"
        else:
            path, last = os.path.join(root, name), False
        try:
            data = open(path, "rb").read()
        except OSError:
            # Answer "not found" at once — never stay silent: a silent answer costs the
            # bootloader a whole TFTP_FILE_TIMEOUT (30 s) per missing file (cmdline.txt did).
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.sendto(struct.pack("!HH", ERROR, 1) + b"File not found\0", client)
            log(f"not found: {name}"); tlog(root, ev="notfound", file=name); return
        t = time.monotonic()
        ok, blk = send_file(client, data, opts)
        dt = time.monotonic() - t
        tlog(root, ev="sent" if ok else "aborted", file=name, bytes=len(data), blksize=blk, ms=round(dt * 1000, 1))
        log(f"{'sent' if ok else 'gave up on'} {name} ({len(data)} B, blk {blk}, {dt*1000:.0f} ms) to {client[0]}"
            + (" — the sentence's image" if last else ""))
        if ok and name == "boot.img":
            served(root, data)
        if ok and last:
            consume(root)
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
    log(f"tftpd on :{port}, root {os.path.abspath(root)}, {'a sentence is pending' if is_pending(root) else 'idle'}")
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
        data = open(img, "rb").read()
        target = a[a.index("--target") + 1] if "--target" in a else None
        if target:                                       # per-target settings (RCFG): pi = watchdog on, Pi I2C clock
            import rtcfg
            data = rtcfg.for_target(data, target)
        # the hub stamps its ticket key in and signs the final bytes (after RCFG): boot.img + boot.sig
        bimg, bsig = seal.seal(data, root=root, manifest={"source": os.path.basename(img), "target": target or "twin"},
                               target=target or "twin")
        intend(root, bimg)                               # from now on only this image gets a ticket
        p = pending_path(root)
        for suf, body in ((SEALED["boot.img"], bimg), (SEALED["boot.sig"], bsig.encode())):
            open(p + suf + ".tmp", "wb").write(body); os.replace(p + suf + ".tmp", p + suf)
        tmp = p + ".tmp"; open(tmp, "wb").write(data); os.replace(tmp, p)      # last: its presence means "pending"
        log(f"pending: {img} ({len(data)} B)" + (f", RCFG for {target}" if target else "") + f", sealed boot.img {len(bimg)} B")
    else:
        serve(int(a[a.index("--port") + 1]) if "--port" in a else 69, root)
