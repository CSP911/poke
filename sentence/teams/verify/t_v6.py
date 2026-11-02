"""V6: the runtime enforces value ranges (first data byte per register) and write counts per device.
FN limits are set in memory only (manifest.py untouched); records isolated.
    ../sim/body/.venv/bin/python teams/verify/t_v6.py"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import tempfile; os.environ["POKE_RECORDS"] = tempfile.mkdtemp(prefix="verify-records-")
import say, manifest as mf
M = lambda extra="": '/* MANIFEST {"v": 1, "uses": [{"fn": "led_set", "targets": [0, 1, 2, 3], "end": "leave"%s}], "max_s": 2, "summary_ko": "LED"} */\n' % extra
OP = '#include "rt.h"\n#include "led.h"\nvoid op_main(void){ %s }'
good = n = 0
def case(name, src, tag, want_leds, want_err, fx_w=None):
    global good, n
    _, _, p = say.build(src, tag)
    b = say.boot(p, say.default_bench())
    f = next((l for l in b["lines"] if l.startswith("POKE-FAULT")), "")
    err = (b["result"] or {}).get("error", "")
    ok = b["powered_off"] and b["truth"].get("leds_on") == want_leds and ((want_err in err and "kind=i2c" in f) if want_err else not f)
    rep = say.effects_report(b, p); ok = ok and not rep["undeclared"]
    n += 1; good += ok
    print(f"{'✓' if ok else '✗'} {name:<60} leds={b['truth'].get('leds_on')} {f[:60]} {err[:60]}")
base = dict(mf.FN["led_set"])
try:
    mf.FN["led_set"].update(ranges={"0x06": [0x54, 0x55]})          # LS0: LED 0 on (00) or off (01) only, LEDs 1–3 off
    case("range: LED 0 on (LS0 = 0x54) is inside", M() + OP % 'led_set(0x60, 0, 0);', "vt-v6-in", [0], None)
    case("range: LED 0 blink (LS0 = 0x56) is outside → refused", M() + OP % 'led_set(0x60, 0, 2); report_int("after", 1);', "vt-v6-out", [], "outside the range")
    mf.FN["led_set"] = dict(base, max_writes=3)
    case("count: 4 writes with a cap of 3 → the 4th is refused", M() + OP % 'for (int i = 0; i < 4; i++) led_set(0x60, i, 0);', "vt-v6-count", [0, 1, 2], "more times")
    case("count: MANIFEST may only lower the cap (asks 2 → 2)", M(', "max_writes": 2') + OP % 'for (int i = 0; i < 3; i++) led_set(0x60, i, 0);', "vt-v6-lower", [0, 1], "more times")
    case("count: MANIFEST cannot raise it (asks 50 → 3)", M(', "max_writes": 50') + OP % 'for (int i = 0; i < 4; i++) led_set(0x60, i, 0);', "vt-v6-raise", [0, 1, 2], "more times")
    # V6b: every data byte of a multi-byte write is range-checked (raw 3-byte writes [reg, d1, d2] to LS0/LS1)
    W = lambda d1, d2, r=0x06: 'i2c_u8 w[3] = {%d, %d, %d}; report_int("e", i2c_write(0x60, w, 3));' % (r, d1, d2)
    lim = dict(base, write_regs=[6, 7], max_write=3, ranges={"6": [0x54, 0x55], "7": [0x55, 0x55]})
    mf.FN["led_set"] = dict(lim)                                     # auto-increment unknown: all bytes vs LS0's range
    # admitted writes: the PCA9552 itself only auto-increments with pointer bit 0x10, so here both bytes land in
    # LS0 and the last one (0x55, all off) stays — what is tested is that the runtime let the write through
    case("unknown AI: [LS0, 0x54, 0x55] — both bytes inside LS0's range", M() + OP % W(0x54, 0x55), "vt-v6b-u-in", [], None)
    case("unknown AI: [LS0, 0x54, 0x50] — 2nd byte outside → refused", M() + OP % W(0x54, 0x50), "vt-v6b-u-out", [], "outside the range")
    mf.FN["led_set"] = dict(lim, auto_increment=True)               # known: byte k goes to register 6+k-1
    case("AI: [LS0, 0x54, 0x55] — LS0 0x54, LS1 0x55 both in their ranges", M() + OP % W(0x54, 0x55), "vt-v6b-a-in", [], None)
    case("AI: [LS0, 0x54, 0x54] — LS1 gets 0x54, outside its range → refused", M() + OP % W(0x54, 0x54), "vt-v6b-a-out", [], "outside the range")
    case("AI: [LS1, 0x55, 0x55] — runs into LS2, not writable → refused", M() + OP % W(0x55, 0x55, 0x07), "vt-v6b-a-past", [], "past the registers")
    mf.FN["led_set"] = dict(base)
    case("no limits in FN: as before (4 writes)", M() + OP % 'for (int i = 0; i < 4; i++) led_set(0x60, i, 0);', "vt-v6-none", [0, 1, 2, 3], None)
finally:
    mf.FN["led_set"] = base
print(f"\n{good}/{n} value/count cases as expected"); sys.exit(0 if good == n else 1)
