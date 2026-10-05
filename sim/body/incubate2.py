"""AI incubation, stage 2: the model writes the driver and proves it works.

For each device it identified (build/incubated/0x??.json, from incubate.py)
the model writes a POKE resident in C that reads the device and answers
SREQ op 1 with a JSON reading. The resident is built, injected into the
edge, asked for a reading, and checked against ground truth we control on
the twin (temperature and magnetic field set through the QEMU monitor, the
RTC against the host clock). Failures — compile errors, crashes, wrong
values — go back to the model, up to 3 rounds. What passes is written as a
library entry draft: build/incubated/<name>/{<name>.c, device.json}.
"""
import os, re, sys, json, time, glob, subprocess, datetime
import pokeedge

ROOT = pokeedge.ROOT
HOST = os.environ.get("POKE_HOST", "twin")
for line in open(os.path.join(ROOT, ".env")):
    m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
    if m and m.group(1) not in os.environ:
        os.environ[m.group(1)] = m.group(2)
import anthropic

MODEL = os.environ.get("POKE_MODEL", "claude-sonnet-5")
I2C_DIR = os.path.join(ROOT, "edge", "library", "pi4", "i2c")
VIRTUAL = ["-device", "tmp105,id=t1,bus=i2c-bus.1,address=0x49",
           "-device", "lsm303dlhc_mag,id=m1,bus=i2c-bus.1,address=0x1e",
           "-device", "ds1338,id=r1,bus=i2c-bus.1,address=0x68",
           "-device", "pca9552,bus=i2c-bus.1,address=0x60"]
MAPS = [(0xFE804000, 0x1000, "device"), (0xFE200000, 0x1000, "device")]

# what the reading must look like, by the kind of device the model said it found
KINDS = [
    ("temperature", re.compile(r"temp", re.I), '{"temp_mC": <int, milli-degrees C>}'),
    ("rtc", re.compile(r"rtc|clock|ds1307|ds1338|ds3231", re.I), '{"utc": "HH:MM:SS", "date": "YYYY-MM-DD"}'),
    ("magnetometer", re.compile(r"magnet|compass|hmc|lsm303", re.I), '{"x": <int raw>, "y": <int raw>, "z": <int raw>}  (raw signed 16-bit counts, X Y Z order as the part defines)'),
]

SYSTEM = """You write device drivers for POKE, a bare-metal system where drivers are small C programs ("residents") injected at run time, running as isolated processes with only the hardware windows they need. There is no libc, no printf, no malloc, no memcpy/memset (don't write code that makes the compiler emit them: no struct copies, no large local array initialisers; use static buffers).

Start the file with:  typedef unsigned long u64; typedef unsigned int u32; typedef unsigned char u8;  (poke_api.h does not define them).
Keep static data small (< 8 KB total). Two-digit zero padding: fmt_pad(out, value, 2).

Contract (poke_api.h):
  u64 resident_main(const api_t *api, u64 op, u64 arg)   — must be the FIRST function and carry __attribute__((section(".text.main")))
  op RES_NAME(4) → return (u64)"<name>";  RES_INIT(1) → configure the device, return 0 (nonzero = failed);
  RES_TICK(2) → called every ~2 ms: poll the service page; RES_STOP(3) → return 0.
  api->log("text") logs a line (≤ 90 chars, no newlines).
Service page: svc_page_t *sp = (svc_page_t *)UNIT_SVC_VA; when sp->req_seq != last_seq, handle sp->op and answer by filling sp->rsp (bytes), sp->rsp_len, sp->status (0 ok), then write sp->rsp_seq = sp->req_seq LAST (after a `__asm__ volatile("dsb sy" ::: "memory")`).
  Op 1 must answer with a JSON text (no trailing NUL needed) of the required shape — it is how the reading is checked.
I2C: #include "i2c_bus.h" gives i2c_init(), i2c_read(addr, reg, buf, n) (reg -1 = no pointer byte), i2c_write(addr, data, n), and fmt_int/fmt_fixed/fmt_str for building the JSON. Call i2c_init() once in RES_INIT.
Output ONE complete C file and nothing else — no markdown fences. Include "../../../kernel/pi4/poke_api.h" exactly like that, and "i2c_bus.h"."""

def kind_of(identity):
    for k, rx, shape in KINDS:
        if rx.search(identity):
            return k, shape
    return None, None

def main():
    found = []
    for f in sorted(glob.glob(os.path.join(pokeedge.BUILD, "incubated", "0x*.json"))):
        d = json.load(open(f))
        if float(d.get("confidence", 0)) >= 0.7:
            found.append(d)
    if not found:
        print("no identified devices (run incubate.py first)"); return
    twin = pokeedge.Twin(extra=VIRTUAL, monitor=True) if HOST == "twin" else None
    host = twin.__enter__() if twin else HOST
    client = anthropic.Anthropic()
    try:
        link = pokeedge.connect(host)
        results = []
        for dev in found:
            addr = dev["addr"] if isinstance(dev["addr"], int) else int(str(dev["addr"]), 16); kind, shape = kind_of(dev["identity"])
            print(f"\n══ 0x{addr:02x}: {dev['identity']} ({kind or 'no ground truth'})")
            if not kind:
                print("   건너뜀: 검증할 정답이 없는 종류"); continue
            name = {"temperature": "i2c-temp", "rtc": "i2c-rtc", "magnetometer": "i2c-mag"}[kind] + f"-{addr:02x}"
            if os.path.exists(os.path.join(pokeedge.BUILD, "incubated", name, "device.json")) and "--redo" not in sys.argv:
                print("   이미 검증됨 (build/incubated/%s) — 건너뜀" % name); results.append((name, dev["identity"], True)); continue
            prompt = (f"Device at I2C address 0x{addr:02x}, identified as: {dev['identity']}.\nEvidence: {dev.get('evidence', '')}\n"
                      f"Read recipe you proposed: {dev.get('read_recipe', '')}\n\nWrite the resident '{name}'. "
                      f"On op 1 answer with JSON of exactly this shape: {shape}. Read the device fresh on every op 1 (do not cache).")
            messages = [{"role": "user", "content": prompt}]
            ok = False
            for rnd in range(1, 4):
                msg = client.messages.create(model=MODEL, max_tokens=8000, system=SYSTEM, messages=messages)
                code = "".join(b.text for b in msg.content if b.type == "text").strip()
                code = re.sub(r"^```\w*\n?", "", code); code = re.sub(r"\n?```$", "", code)
                messages.append({"role": "assistant", "content": code})
                src_dir = os.path.join(pokeedge.BUILD, "incubated", name); os.makedirs(src_dir, exist_ok=True)
                src = os.path.join(src_dir, f"{name}.c"); open(src, "w").write(code + "\n")
                # build
                try:
                    img = build_resident(src, name)
                    sym = subprocess.run(["aarch64-elf-nm", os.path.join(pokeedge.BUILD, f"{name}.elf")], capture_output=True, text=True).stdout
                    at0 = re.search(r"^0+ T resident_main$", sym, re.M)
                    if not img or not at0:
                        raise RuntimeError(f"the image is {len(img)} bytes and resident_main is not at offset 0: "
                                           f"resident_main must be the FIRST function in the file and carry __attribute__((section(\".text.main\"))). nm says:\n{sym[:400]}")
                except RuntimeError as e:
                    err = str(e)[:1200]; print(f"   라운드 {rnd}: 컴파일 실패 — {err.splitlines()[0][:120]}")
                    messages.append({"role": "user", "content": f"It did not compile:\n{err}\nFix it and output the whole file again."}); continue
                # inject (replaces whatever resident runs)
                info = json.loads(link.request(b"INFO", timeout=2))
                if info.get("resident"):
                    link.request(b"RSTP", timeout=5)
                r = pokeedge.run_resident(link, img, MAPS, name)
                if "error" in r:
                    print(f"   라운드 {rnd}: 주입 실패 — {r}")
                    messages.append({"role": "user", "content": f"The resident ({len(img)} bytes) failed to start on the device: {json.dumps(r)}. "
                                     "If 'init failed', RES_INIT returned nonzero or crashed (check the device log in last_log). Fix it and output the whole file again."}); continue
                # verify against ground truth (twice, with a change in between when we can cause one)
                verdict, detail = verify(link, twin, kind)
                print(f"   라운드 {rnd}: {name} {len(img)} B 주입 → {detail}")
                if verdict:
                    ok = True; break
                messages.append({"role": "user", "content": f"The resident runs but its reading is wrong: {detail}. Fix it and output the whole file again."})
            results.append((name, dev["identity"], ok))
            if ok:
                entry = {"name": name, "arch": "pi4", "kind": "resident", "provides": [kind], "binary": "resident.bin", "source": f"{name}.c",
                         "description": f"{dev['identity']} at I2C 0x{addr:02x} on BSC1 — driver written and verified by {MODEL} during AI incubation",
                         "evidence": dev.get("evidence"), "service": {"op1": shape},
                         "mappings": [{"pa": "0xFE804000", "len": "0x1000", "attr": "device", "what": "BSC1"}, {"pa": "0xFE200000", "len": "0x1000", "attr": "device", "what": "GPIO (pins to ALT0)"}],
                         "incubated": {"by": MODEL, "at": time.strftime("%Y-%m-%d %H:%M"), "rounds": rnd, "verified_on": "twin" if twin else host}}
                json.dump(entry, open(os.path.join(src_dir, "device.json"), "w"), indent=2, ensure_ascii=False)
                print(f"   ✦ 라이브러리 항목 초안: build/incubated/{name}/")
        print("\n[2단계 결과]")
        for name, ident, ok in results:
            print(f"  {'✓' if ok else '✗'} {name:<16} {ident[:70]}")
    finally:
        if twin:
            twin.__exit__(None, None, None)

def build_resident(src, name):
    return pokeedge.build_resident(src, name, defines=[], include=I2C_DIR)

def reading(link):
    st, d = pokeedge.sreq(link, 1, timeout=8)
    if st:
        raise RuntimeError(f"op 1 status {st}")
    try:
        return json.loads(d.decode(errors="replace").rstrip("\0"))
    except Exception:
        raise RuntimeError(f"op 1 answered non-JSON: {d[:80]!r}")

def verify(link, twin, kind):
    try:
        if kind == "temperature":
            vals = []
            for truth in (27500, -3250, 41125):
                if twin: twin.monitor(f"qom-set t1 temperature {truth}"); time.sleep(0.05)
                got = reading(link).get("temp_mC"); vals.append((truth, got))
                if got is None or abs(int(got) - truth) > 600:     # the model may be 9-bit (0.5 C steps)
                    return False, f"set {truth / 1000:.3f} C, resident read {got} mC (pairs so far {vals})"
            return True, "온도 3회 변경 모두 추종: " + ", ".join(f"{t / 1000:g}→{g / 1000:g} C" for t, g in vals)
        if kind == "magnetometer":
            # QEMU's lsm303dlhc_mag takes mag-x/y/z in field units where 100000 reads back as 1100 counts
            # (1 gauss at the default gain), range about ±1.86 G — so truth is chosen in counts and converted
            # ... and the model refuses negative values and scales Z by 980/1100 (the part's Z gain), so truth
            # is non-negative and the expected Z count is derived with that gain
            vals = []
            for truth in ((100, 200, 300), (1500, 40, 1800)):
                if twin:
                    for ax, v in zip("xyz", truth): twin.monitor(f"qom-set m1 mag-{ax} {round(v * 100000 / 1100)}")
                    time.sleep(0.05)
                expect = (truth[0], truth[1], round(truth[2] * 980 / 1100))
                r = reading(link); got = (r.get("x"), r.get("y"), r.get("z")); vals.append((expect, got))
                if any(g is None for g in got) or any(abs(int(g) - t) > 3 for g, t in zip(got, expect)):
                    return False, f"set x,y,z={truth} (expected counts {expect}), resident read {got} (pairs so far {vals})"
            return True, "자기장 2회 변경 모두 추종: " + "; ".join(f"{t}→{g}" for t, g in vals)
        if kind == "rtc":
            r = reading(link); now = datetime.datetime.now(datetime.timezone.utc)
            try:
                hh, mm, ss = (int(x) for x in r["utc"].split(":")); y, mo, d = (int(x) for x in r["date"].split("-"))
            except Exception:
                return False, f"unparseable {r}"
            got = datetime.datetime(y, mo, d, hh, mm, ss, tzinfo=datetime.timezone.utc)
            delta = abs((got - now).total_seconds())
            if delta > 120:
                return False, f"resident says {got.isoformat()}, host UTC is {now.isoformat()} ({delta:.0f} s apart)"
            return True, f"RTC {r['date']} {r['utc']} UTC, 호스트와 {delta:.0f}초 차이"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    return False, "no verifier"

if __name__ == "__main__":
    main()
