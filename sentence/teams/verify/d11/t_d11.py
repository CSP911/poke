"""D11 candidate (verify team): tmp105.h at 12 bits, with manifest.FN allowing its config write.
Builds with this directory's tmp105.h in front of frags/, patches FN in memory, boots on the twin."""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))
import say, manifest
manifest.FN["tmp105_read_mC"].update(write_regs=[0x01], max_write=2)
say.CFLAGS = ["-I", HERE, *say.CFLAGS]; say.FRAGS = HERE   # this dir stands in for frags/ (tmp105 only)
SRC = '#include "rt.h"\n#include "tmp105.h"\nvoid op_main(void){ long t; int e = tmp105_read_mC(0x48, &t); if (e) { report_int("e", e); fail("tmp105"); return; } report_int("mC", t); }'
img = say.build(SRC, "vt-d11")[2]
good = 0
for mC in (25250, -10250, 23750, 124933):
    b = say.boot(img, {"t1 temperature": mC})
    want = (mC * 16 // 1000) * 1000 // 16 if mC >= 0 else -((-mC * 16 // 1000) * 1000 // 16)
    ok = b["powered_off"] and b["result"] == {"mC": want}
    good += ok
    print("✓" if ok else "✗", mC, "→", b["result"], "node_ms", b["node_ms"])
print(f"{good}/4")
