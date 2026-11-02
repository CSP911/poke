#!/usr/bin/env python3
"""The edge's I2C bus service and the project's knowledge, as a command line —
the only tools a Claude Code incubation agent is allowed to run.

    bus.py scan                       → addresses that acknowledge
    bus.py read ADDR [REG] N          → N bytes (REG omitted or 'none' = no register pointer)
    bus.py write ADDR BYTE [BYTE …]   → write bytes (first is usually the register pointer)
    bus.py knowledge [PATH]           → RouteMind table: areas, or one area's nodes
    bus.py knowledge-read PATH        → one node's text

Numbers may be decimal or 0x-hex. The edge is reached through the address in
build/cc/edge.txt (written by incubate_cc.py: the QEMU twin's tcp://… or a Pi's IP).
"""
import os, sys, json, struct
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pokeedge

EDGE_FILE = os.path.join(HERE, "build", "cc", "edge.txt")
LOG = os.path.join(HERE, "build", "cc", "bus-log.jsonl")

def num(s):
    return int(s, 0)

def log(entry):
    with open(LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")

def link():
    host = open(EDGE_FILE).read().strip()
    return pokeedge.connect(host)

def main(argv):
    if not argv:
        print(__doc__); return 2
    cmd, a = argv[0], argv[1:]
    if cmd == "scan":
        st, d = pokeedge.sreq(link(), 1)
        addrs = [hex(x) for x in d[1:1 + d[0]]]
        log({"op": "scan", "out": addrs}); print("acknowledged:", " ".join(addrs) or "(none)")
    elif cmd == "read":
        if len(a) == 2:
            addr, reg, n = num(a[0]), 0xFF, num(a[1])
        elif len(a) == 3:
            addr, n = num(a[0]), num(a[2]); reg = 0xFF if a[1].lower() == "none" else num(a[1])
        else:
            print("usage: read ADDR [REG] N"); return 2
        n = max(1, min(32, n))
        st, d = pokeedge.sreq(link(), 2, bytes([addr, reg, n]))
        out = "no ACK" if st == 2 else (f"error {st}" if st else d.hex(" "))
        log({"op": "read", "addr": addr, "reg": None if reg == 0xFF else reg, "n": n, "out": out}); print(out)
    elif cmd == "write":
        if len(a) < 2:
            print("usage: write ADDR BYTE [BYTE …]"); return 2
        addr = num(a[0]); data = bytes(num(x) & 0xFF for x in a[1:])
        st, _ = pokeedge.sreq(link(), 3, bytes([addr]) + data)
        out = "ok" if st == 0 else ("no ACK" if st == 2 else f"error {st}")
        log({"op": "write", "addr": addr, "data": data.hex(), "out": out}); print(out)
    elif cmd in ("knowledge", "knowledge-read"):
        import routemind
        if not routemind.available():
            print("(no knowledge service running)"); return 0
        txt = routemind.read(a[0]) if cmd == "knowledge-read" else routemind.table(a[0] if a else None)
        log({"op": cmd, "path": a[0] if a else None, "chars": len(txt)}); print(txt)
    else:
        print(__doc__); return 2
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except pokeedge.EdgeError as e:
        print(f"edge error: {e}"); sys.exit(1)
