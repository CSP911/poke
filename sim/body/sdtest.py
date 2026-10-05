"""SD card incubation on a POKE edge (the Pi, or the twin by default).

    POKE_HOST=10.0.0.2 .venv/bin/python sdtest.py        # real Pi 4 (EMMC2)
    .venv/bin/python sdtest.py                            # QEMU twin (EMMC @0xFE300000, build/sd-test.img)

1. read-only probe: controller, card identity, MBR partitions
2. write+read-back on a block the partitions do not use
3. the resident driver: inject, then SREQ info / read / write / read-back
"""
import os, re, struct, json, time
import pokeedge

SD = os.path.join(pokeedge.ROOT, "edge", "library", "pi4", "sdcard")
HOST = os.environ.get("POKE_HOST", "twin")
twin = HOST == "twin"
defines = ["EMMC_BASE=0xFE300000UL"] if twin else []
MAP = [(0xFE300000 if twin else 0xFE340000, 0x1000, "device")]

def show(r):
    for l in (r.get("log") or "").split("\n"):
        if l.strip(): print("   ", l)
    return r.get("code")

def main():
    probe = pokeedge.build_unit(os.path.join(SD, "sdprobe.c"), "sdprobe", defines=defines)
    res = pokeedge.build_resident(os.path.join(SD, "sdcard.c"), "sdcard", defines=defines)
    ctx = pokeedge.Twin(sd=os.path.join(pokeedge.BUILD, "sd-test.img")) if twin else None
    host = ctx.__enter__() if ctx else HOST
    try:
        link = pokeedge.connect(host)
        print(f"edge: {host}\n\n[1] read-only probe")
        r = pokeedge.run_unit(link, pokeedge.patch_params(probe, [0, 0]), MAP)
        code = show(r)
        if code != 0:
            print(f"   probe failed with code {code}"); return
        starts = [int(m) for m in re.findall(r"start (\d+)", r.get("log") or "")]
        gap_end = min(starts) if starts else 0
        if gap_end < 64:
            print("   no safe scratch block below the first partition; skipping the write test"); scratch = 0
        else:
            scratch = gap_end - 8                              # inside the unused gap, well clear of the MBR
            print(f"\n[2] write + read-back on block {scratch} (first partition starts at {gap_end})")
            code = show(pokeedge.run_unit(link, pokeedge.patch_params(probe, [1, scratch]), MAP))
            if code != 0:
                print(f"   write test failed with code {code}"); return
        print("\n[3] resident driver")
        print("   RSLD →", pokeedge.run_resident(link, res, MAP, "sdcard"))
        info = json.loads(link.request(b"INFO", timeout=2))
        print("   INFO resident:", info.get("resident"))
        st, d = pokeedge.sreq(link, 1); sdhc, cap, *cid, rca = struct.unpack("<7I", d[:28])
        print(f"   info: {'SDHC' if sdhc else 'SDSC'} {cap} MB, rca 0x{rca:x}")
        st, d = pokeedge.sreq(link, 2, struct.pack("<II", 0, 1)); print(f"   read lba 0: status {st}, sig {d[510:512].hex()}")
        if scratch:
            payload = bytes((i * 13 + 5) & 0xff for i in range(512))
            st, _ = pokeedge.sreq(link, 3, struct.pack("<I", scratch) + payload)
            st2, d = pokeedge.sreq(link, 2, struct.pack("<II", scratch, 1))
            print(f"   write lba {scratch}: status {st}; read back: status {st2}, match {d == payload}")
        t0 = time.time(); n = 0
        for lba in range(gap_end, gap_end + 64):
            pokeedge.sreq(link, 2, struct.pack("<II", lba, 2)); n += 2
        print(f"   throughput: {n * 512 / 1024 / (time.time() - t0):.0f} KB/s (2 blocks per request)")
        print("   PING →", link.request(b"PING"))
    finally:
        if ctx:
            ctx.__exit__(None, None, None)

if __name__ == "__main__":
    main()
