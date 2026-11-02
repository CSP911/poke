#!/usr/bin/env python3
"""rtcfg — the image's per-target settings block (RCFG, edge/sentence/rt_power.c). 실기기팀 소유.

The hub builds one image per op; the code bytes are the same everywhere. What differs between the
twin and a real Pi is written into the RCFG block inside the image bytes:

  field      twin    pi      meaning
  wdog       0       1       PM watchdog (QEMU resets the moment it is armed, so never on the twin;
                             2 = twin with the Pi's quiet limit but without the PM watchdog)
  budget_ms  20000   20000   the op's whole run (GIC deadline)
  cdiv       1500    5000    I2C bus 1 divider for 100 kHz = core clock / 100 kHz
                             (twin: QEMU ignores it; Pi 4: core clock 500 MHz, pinned by
                             core_freq=500/core_freq_min=500 in netboot/root/config.txt)
  quiet_ms   14000   14000   with wdog: the longest an op may go without a runtime call (< 15 s watchdog)
  halt       0       0       1 = power off for real after the result (PM_RSTS partition 63)

    from rtcfg import read, patch, for_target
    data = for_target(open(img, "rb").read(), "pi")          # bytes → bytes
    rtcfg.py show IMAGE
    rtcfg.py pi|twin IMAGE [-o OUT] [budget_ms=… cdiv=… halt=1 …]
"""
import struct, sys

MAGIC = 0x47464352                      # "RCFG"
FIELDS = ("magic", "version", "wdog", "budget_ms", "cdiv", "quiet_ms", "halt", "reserved")
FMT = "<8I"
VERSION = 1
I2C_HZ = 100_000
CORE_HZ = {"twin": 150_000_000, "pi": 500_000_000}
TARGETS = {
    "twin": {"wdog": 0, "budget_ms": 20000, "cdiv": CORE_HZ["twin"] // I2C_HZ, "quiet_ms": 14000, "halt": 0},
    "pi":   {"wdog": 1, "budget_ms": 20000, "cdiv": CORE_HZ["pi"] // I2C_HZ, "quiet_ms": 14000, "halt": 0},
}
WDOG_MAX_MS = 0xFFFFF * 1000 // 65536    # ~15999 ms: the PM watchdog's 20-bit counter at 65536 Hz

class RcfgError(RuntimeError):
    pass

def cdiv_for(core_hz, i2c_hz=I2C_HZ):
    """BSC CDIV for a bus speed; the hardware rounds odd values down, so keep it even."""
    return max(2, (core_hz // i2c_hz) & ~1)

def _find(img):
    at = [i for i in range(0, len(img) - 7, 4) if struct.unpack_from("<II", img, i) == (MAGIC, VERSION)]
    if len(at) != 1:
        raise RcfgError(f"RCFG block not found exactly once in the image ({len(at)}): built without rt_power.c?")
    return at[0]

def read(img):
    return dict(zip(FIELDS, struct.unpack_from(FMT, img, _find(img))))

def check(cfg):
    """Rules every image must keep, whatever the target."""
    probs = []
    if cfg["wdog"] not in (0, 1, 2): probs.append("wdog must be 0, 1 or 2")
    if cfg["wdog"] == 1 and not (0 < cfg["quiet_ms"] < 15000):
        probs.append(f"quiet_ms {cfg['quiet_ms']} must be under the watchdog's 15 s")
    if not (100 <= cfg["budget_ms"] <= 60000): probs.append("budget_ms out of range (100..60000)")
    if not (2 <= cfg["cdiv"] <= 0xFFFE) or cfg["cdiv"] & 1: probs.append("cdiv must be even, 2..65534")
    if cfg["halt"] not in (0, 1): probs.append("halt must be 0 or 1")
    return probs

def patch(img, **vals):
    at = _find(img)
    cfg = dict(zip(FIELDS, struct.unpack_from(FMT, img, at)))
    for k, v in vals.items():
        if k not in FIELDS[2:7]:
            raise RcfgError(f"unknown RCFG field {k}")
        cfg[k] = int(v)
    probs = check(cfg)
    if probs:
        raise RcfgError("; ".join(probs))
    out = bytearray(img)
    struct.pack_into(FMT, out, at, *(cfg[f] for f in FIELDS))
    return bytes(out)

def for_target(img, target, **over):
    if target not in TARGETS:
        raise RcfgError(f"unknown target {target} (twin, pi)")
    return patch(img, **{**TARGETS[target], **over})

if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) >= 2 and a[0] == "show":
        c = read(open(a[1], "rb").read()); print(c, "problems:", check(c) or "none")
    elif len(a) >= 2 and a[0] in TARGETS:
        out = a[a.index("-o") + 1] if "-o" in a else a[1]
        over = dict(x.split("=", 1) for x in a[2:] if "=" in x)
        data = for_target(open(a[1], "rb").read(), a[0], **over)
        open(out, "wb").write(data); print(out, read(data))
    else:
        print(__doc__); sys.exit(2)
