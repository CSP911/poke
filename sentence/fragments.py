"""Turn incubated, verified drivers into fragments: header-only pieces the hub
assembles into sentence images. The driver never lives on the node; it lives
here, and joins an image only when a sentence needs it.

    python3 fragments.py            # regenerate edge/sentence/frags/*.h + catalog.json

Each fragment is written by Claude Code (claude -p) from the incubated driver
source and its verification record, to a fixed function signature, then
compiled once to make sure it builds.
"""
import os, re, sys, json, subprocess, concurrent.futures as cf
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "sim", "body"))
import claude_cli

SRC = os.path.join(ROOT, "sim", "body", "build", "cc-run", "incubated")
EDGE = os.path.join(ROOT, "edge", "sentence")
FRAGS = os.path.join(EDGE, "frags")

SPECS = {
    "tmp105": {"from": "i2c-temp-4a", "default_addr": "0x48",
               "functions": ["static int tmp105_read_mC(unsigned addr, long *mC)  /* temperature in milli-degrees C; 0 = ok, else the i2c error code */"]},
    "rtc": {"from": "i2c-rtc-68", "default_addr": "0x68",
            "functions": ["static int rtc_read(unsigned addr, int *year, int *month, int *day, int *hour, int *min, int *sec)  /* 24-hour, year as 4 digits; 0 = ok */",
                          "static int rtc_set(unsigned addr, int year, int month, int day, int hour, int min, int sec)  /* 0 = ok */"]},
    "mag": {"from": "i2c-mag-1e", "default_addr": "0x1e",
            "functions": ["static int mag_read(unsigned addr, int *x, int *y, int *z)  /* raw signed counts; puts the part into measurement mode if needed; 0 = ok */"]},
    "led": {"from": "i2c-led-61", "default_addr": "0x60",
            "functions": ["static int led_set(unsigned addr, int led, int mode)  /* led 0..15; mode 0 = on, 1 = off, 2 = blink rate 0 (PWM0), 3 = blink rate 1 (PWM1); 0 = ok */",
                          "static int led_inputs(unsigned addr, unsigned *pins)  /* 16 pin levels, bit i = pin i (an LED that is on reads 0); 0 = ok */"]},
}

SYSTEM = """You turn a verified POKE resident driver into a FRAGMENT: a header-only piece of a bare-metal image (no OS, no libc, core 0; the op and its fragments run unprivileged and reach the bus only through the runtime). Output ONE C header and nothing else — no markdown fences.

Rules:
- First line exactly: // FRAG {"name": "<name>", "use_when": "<3-5 situations separated by ' · ' in an agent's words>", "functions": ["<signature>", ...]}
- Include guard, then #include "i2c_bus.h". That header provides: i2c_init() (call it before the first transfer; calling it again is harmless), i2c_read(addr, reg, buf, n) (reg -1 = no register pointer), i2c_write(addr, data, n); types i2c_u8 / i2c_u32. Returns 0 ok, 1 bad request, 2 no ACK, 3 clock stretch, 4 never finished. Each transfer is checked against the image's allow list (the hub's manifest.FN entry for each function: device address, registers it may write, longest write), so write only the registers the function really needs — and tell the hub team which ones.
- Every function `static`. No globals except `static const` tables. No resident_main, no service page, no api_t, no syscalls, no printf, no memcpy/memset (no struct copies, no big local initialisers).
- Provide exactly the requested functions with exactly the requested signatures. The I2C address is a parameter.
- Keep the register knowledge from the driver you are given (it was verified against the device): register addresses, value formats, mode bits, conversions."""

def make(name, spec):
    d = os.path.join(SRC, spec["from"])
    src = open(os.path.join(d, spec["from"] + ".c")).read()
    dev = open(os.path.join(d, "device.json")).read()
    user = (f"Fragment name: {name}\nRequested functions:\n" + "\n".join("  " + f for f in spec["functions"]) +
            f"\n\nThe verified resident driver:\n{src}\n\nIts verification record:\n{dev}")
    text = claude_cli.strip_fences(claude_cli.ask(SYSTEM, user))
    path = os.path.join(FRAGS, f"{name}.h")
    open(path, "w").write(text + "\n")
    # compile check: every requested function must exist and the header must build alone
    probe = os.path.join(FRAGS, f".check_{name}.c")
    calls = []
    for f in spec["functions"]:
        fn = re.search(r"static int (\w+)\(", f).group(1); calls.append(f"(void){fn};")
    open(probe, "w").write(f'#include "{name}.h"\nvoid check(void) {{ {" ".join(calls)} }}\n')
    r = subprocess.run(["aarch64-elf-gcc", "-ffreestanding", "-nostdlib", "-fno-builtin", "-O2", "-Wall", "-Werror=implicit-function-declaration",
                        "-mcpu=cortex-a72", "-mstrict-align", "-mgeneral-regs-only", "-I", EDGE, "-I", FRAGS, "-c", probe, "-o", "/dev/null"],
                       capture_output=True, text=True)
    os.remove(probe)
    meta = json.loads(re.match(r"//\s*FRAG\s+(\{.*\})", text).group(1)) if re.match(r"//\s*FRAG\s+(\{.*\})", text) else {"name": name}
    return name, {**meta, "header": f"{name}.h", "default_addr": spec["default_addr"], "from_driver": spec["from"],
                  "functions": spec["functions"], "builds": r.returncode == 0, "build_error": r.stderr[-600:] if r.returncode else ""}

def main():
    os.makedirs(FRAGS, exist_ok=True)
    only = sys.argv[1:] or list(SPECS)
    catalog_path = os.path.join(FRAGS, "catalog.json")
    catalog = json.load(open(catalog_path)) if os.path.exists(catalog_path) else {}
    with cf.ThreadPoolExecutor(4) as ex:
        for name, entry in ex.map(lambda n: make(n, SPECS[n]), only):
            catalog[name] = entry
            print(f"{name:<7} {'builds' if entry['builds'] else 'BUILD FAILED'}  use_when: {entry.get('use_when', '')[:90]}")
            if not entry["builds"]: print("   ", entry["build_error"][:300])
    json.dump(catalog, open(catalog_path, "w"), indent=2, ensure_ascii=False)

if __name__ == "__main__":
    main()
