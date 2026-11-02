"""R3: scan images read only. say.scan_allowlist(addrs) + say.build(src, tag, allow=…): every address may be
read (no register pointer), nothing may be written — not even one byte.
    ../sim/body/.venv/bin/python teams/verify/t_r3.py"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import tempfile; os.environ["POKE_RECORDS"] = tempfile.mkdtemp(prefix="verify-records-")   # never the owner's records
import say
ADDRS = [0x48, 0x49, 0x60, 0x68, 0x1e, 0x20, 0x3c, 0x4c]
READ = ('#include "rt.h"\n#include "i2c_bus.h"\nstatic const unsigned A[8] = {%s};\n'
        'void op_main(void){ i2c_u8 b[1]; unsigned m = 0; for (int i = 0; i < 8; i++) if (!i2c_read(A[i], -1, b, 1)) m |= 1u << i; report_int("ack", m); }') % ", ".join(map(hex, ADDRS))
POINTER = '#include "rt.h"\n#include "i2c_bus.h"\nvoid op_main(void){ i2c_u8 b[1]; report_int("e", i2c_read(0x60, 0x06, b, 1)); }'
WRITE0 = '#include "rt.h"\n#include "i2c_bus.h"\nvoid op_main(void){ i2c_u8 b[1] = {0}; report_int("e", i2c_write(0x60, b, 0)); }'
good = 0
def boot(src, tag):
    _, _, p = say.build(src, tag, allow=say.scan_allowlist(ADDRS))
    b = say.boot(p, say.default_bench())
    return b, p, next((l for l in b["lines"] if l.startswith("POKE-FAULT")), "")
b, p, f = boot(READ, "vt-r3-scan")
want = sum(1 << i for i, a in enumerate(ADDRS) if a in (0x48, 0x60, 0x68, 0x1e))          # the base bench
fx = say.effects_report(b, p)
ok = not f and b["result"] == {"ack": want} and fx["ok"] and all(d["w"] == 0 for d in b["effects"]["dev"])
print(f"{'✓' if ok else '✗'} 8 addresses read, nothing written: ack={b['result']} (want {want}) effects writes={[d['w'] for d in b['effects']['dev']]}"); good += ok
side = json.load(open(p[:-4] + ".i2c.json"))
ok = side["source"] == "given" and all(d["pointer"] is False and d["write_regs"] == [] for d in side["allowlist"]["devices"])
print(f"{'✓' if ok else '✗'} sidecar records the given scan list (effects_report checks against it)"); good += ok
for name, src in (("register pointer (1-byte write)", POINTER), ("address probe (0-byte write)", WRITE0)):
    b, p, f = boot(src, "vt-r3-" + name.split()[0])
    ok = "kind=i2c" in f and "may only read" in (b["result"] or {}).get("error", "") and b["truth"].get("leds_on") == []
    print(f"{'✓' if ok else '✗'} a scan image's {name} is refused: {f}"); good += ok
try:
    say.scan_allowlist(range(0x08, 0x12)); ok = False
except RuntimeError:
    ok = True
print(f"{'✓' if ok else '✗'} more than {say.I2CA_MAX} addresses in one scan image is refused"); good += ok
print(f"\n{good}/5 scan cases as expected"); sys.exit(0 if good == 5 else 1)
