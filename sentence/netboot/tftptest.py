"""Test the hub's tftpd the way the Pi bootloader talks to it (RRQ + blksize/tsize options)."""
import os, sys, time, socket, struct, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "root")

def fetch(port, name, blksize=1024, timeout=2.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(timeout)
    s.sendto(struct.pack("!H", 1) + name.encode() + b"\0octet\0blksize\0" + str(blksize).encode() + b"\0tsize\0" + b"0\0", ("127.0.0.1", port))
    out, blk, expect = b"", 512, 1
    try:
        while True:
            pkt, srv = s.recvfrom(4096)
            op = struct.unpack("!H", pkt[:2])[0]
            if op == 6:                                           # OACK
                opts = pkt[2:].split(b"\0"); d = {opts[i].decode(): opts[i + 1].decode() for i in range(0, len(opts) - 1, 2) if opts[i]}
                blk = int(d.get("blksize", 512)); s.sendto(struct.pack("!HH", 4, 0), srv); continue
            if op == 3:
                n = struct.unpack("!H", pkt[2:4])[0]
                if n == expect & 0xFFFF:
                    out += pkt[4:]; expect += 1
                s.sendto(struct.pack("!HH", 4, n), srv)
                if len(pkt) - 4 < blk:
                    return out
            if op == 5:
                return None
    except socket.timeout:
        return None if not out else out

def main():
    port = 16969
    for f in ("pending.img", "pending.img.booted"):
        if os.path.exists(os.path.join(ROOT, f)): os.remove(os.path.join(ROOT, f))
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "tftpd.py"), "--port", str(port)], stdout=open("/tmp/tftpd.log", "w"), stderr=subprocess.STDOUT)
    time.sleep(0.5)
    ok = True
    try:
        r = fetch(port, "start4.elf"); print("idle request:", "no answer (correct — the node keeps waiting in its bootloader)" if r is None else "ANSWERED (wrong)"); ok &= r is None
        img = sys.argv[1]
        subprocess.run([sys.executable, os.path.join(HERE, "tftpd.py"), "--pend", img], check=True, capture_output=True)
        t0 = time.time()
        fw = fetch(port, "1a2b3c4d/start4.elf"); good = fw == open(os.path.join(ROOT, "start4.elf"), "rb").read()
        print(f"pending: start4.elf {len(fw or b'')} B {'identical' if good else 'MISMATCH'} (serial-number prefix ignored), {time.time() - t0:.2f} s"); ok &= good
        cfg = fetch(port, "config.txt"); print("config.txt:", (cfg or b"").decode().strip().splitlines()[1:3]); ok &= cfg is not None
        k = fetch(port, "sentence.img"); good = k == open(img, "rb").read()
        print(f"sentence image {len(k or b'')} B {'identical' if good else 'MISMATCH'}"); ok &= good
        for _ in range(50):                                   # the server marks it consumed after the last ACK
            consumed = not os.path.exists(os.path.join(ROOT, "pending.img")) and os.path.exists(os.path.join(ROOT, "pending.img.booted"))
            if consumed: break
            time.sleep(0.02)
        print("consumed after one boot:", consumed); ok &= consumed
        r = fetch(port, "start4.elf"); print("next request:", "no answer (correct — idle again)" if r is None else "ANSWERED (wrong)"); ok &= r is None
        # parked: a request made while idle is answered the moment a sentence is pending
        import threading
        got = {}
        def waiting(): got["fw"] = fetch(port, "1a2b3c4d/start4.elf", blksize=1468, timeout=8)
        th = threading.Thread(target=waiting); th.start(); time.sleep(1.0)
        subprocess.run([sys.executable, os.path.join(HERE, "tftpd.py"), "--pend", img], check=True, capture_output=True)
        tp = time.time(); th.join(); dt = time.time() - tp
        good = got.get("fw") == open(os.path.join(ROOT, "start4.elf"), "rb").read() and dt < 1.0
        print(f"parked request answered {dt:.2f} s after the sentence: {'yes' if good else 'NO'}"); ok &= good
        k = fetch(port, "sentence.img"); ok &= k == open(img, "rb").read()
        for _ in range(50):
            if not os.path.exists(os.path.join(ROOT, "pending.img")): break
            time.sleep(0.02)
        # a retransmitted request for a running transfer starts no second transfer
        subprocess.run([sys.executable, os.path.join(HERE, "tftpd.py"), "--pend", img], check=True, capture_output=True)
        c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); c.settimeout(2)
        rrq = struct.pack("!H", 1) + b"sentence.img\0octet\0blksize\0" + b"512\0"
        c.sendto(rrq, ("127.0.0.1", port)); c.sendto(rrq, ("127.0.0.1", port))
        tids = set()
        try:
            while True:
                pkt, srv_addr = c.recvfrom(4096)
                if struct.unpack("!H", pkt[:2])[0] == 6: tids.add(srv_addr)
        except socket.timeout:
            pass
        print("duplicate request → transfers started:", len(tids)); ok &= len(tids) == 1
        os.path.exists(os.path.join(ROOT, "pending.img")) and os.remove(os.path.join(ROOT, "pending.img"))
    finally:
        srv.terminate()
    print(open("/tmp/tftpd.log").read())
    print("tftpd", "PASS" if ok else "FAIL")

if __name__ == "__main__":
    main()
