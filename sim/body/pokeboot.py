"""`pokeboot`: wake a POKE edge and give it back what it learned.

    POKE_HOST=10.0.0.2 .venv/bin/python pokeboot.py [--kernel] [--driver NAME] [--skill NAME]

1. (--kernel) reload the current kernel build over the network
2. inject the SD driver (the one thing that must come from the hub)
3. read the edge store: what drivers and skills does this device remember?
4. hand the device its own memory: the chosen driver from the store's bytes
   (not from the Mac), and the chosen skill into the skill slot

One resident slot for now, so the SD driver steps aside for the device
driver after everything needed has been read.
"""
import os, sys, json, base64, struct, subprocess, time
import pokeedge, edgestore

ROOT = pokeedge.ROOT
host = os.environ.get("POKE_HOST", "twin")
args = sys.argv[1:]
twin = pokeedge.Twin(sd=os.path.join(pokeedge.BUILD, "sd-test.img")) if host == "twin" else None
if twin:
    host = twin.__enter__()
def opt(name, default=None):
    return args[args.index(name) + 1] if name in args else default
driver_name = opt("--driver", "usb-touch-egalax")
skill_name = opt("--skill", "finger/fist")

def poke(*a):
    return subprocess.run([os.path.join(ROOT, "bin", "poke"), *a], capture_output=True, text=True).stdout.strip()

t0 = time.time()
if "--kernel" in args:
    print("[1] kernel:", poke("kernel").splitlines()[-1])
link = pokeedge.connect(host)
info = json.loads(link.request(b"INFO", timeout=2))
print(f"[1] edge {host}: build {info.get('build')}, resident {info.get('resident')}")

sd = json.load(open(f"{ROOT}/edge/library/pi4/sdcard/device.json"))
maps = [(int(m["pa"], 16), int(m["len"], 16), m["attr"]) for m in sd["mappings"]]
sd_bin = open(f"{ROOT}/edge/library/pi4/sdcard/resident.bin", "rb").read()
if twin:                                                                 # QEMU keeps its card on the older EMMC
    maps = [(0xFE300000, 0x1000, "device")]
    sd_bin = pokeedge.build_resident(f"{ROOT}/edge/library/pi4/sdcard/sdcard.c", "sdcard-twin", defines=["EMMC_BASE=0xFE300000UL"])
if info.get("resident") != "sdcard":
    if info.get("resident"):
        link.request(b"RSTP", timeout=5)
    r = pokeedge.run_resident(link, sd_bin, maps, "sdcard")
    print("[2] SD driver:", r)
else:
    print("[2] SD driver already running")

st = edgestore.EdgeStore(link).open()
recs = st.list()
print(f"[3] the device remembers {len(recs)} things:")
for r in recs:
    print(f"      {r['kind']:<9} {r['name']:<28} {r['size']:6d} B")

want_driver = next((r for r in recs if r["name"] == f"driver/{driver_name}"), None)
want_meta = next((r for r in recs if r["name"] == f"driver/{driver_name}.json"), None)
want_skill = next((r for r in recs if r["name"] == skill_name), None)
drv = st.get(want_driver["name"]) if want_driver else None
meta = json.loads(st.get(want_meta["name"])) if want_meta else None
skill = json.loads(st.get(want_skill["name"])) if want_skill else None

if drv and meta and twin and meta["name"] != "sdcard":
    print(f"[4] driver '{meta['name']}' is in the store, but the twin has no {meta['provides']} hardware — kept for the Pi")
elif drv and meta:
    link.request(b"RSTP", timeout=5)                                   # SD driver steps aside
    dm = [(int(m["pa"], 16), int(m["len"], 16), m["attr"]) for m in meta["mappings"]]
    r = pokeedge.run_resident(link, drv, dm, meta["name"])
    print(f"[4] driver '{meta['name']}' from the device's own store ({len(drv)} B):", r)
else:
    print(f"[4] no driver '{driver_name}' in the store")

if skill and skill.get("image_b64"):
    img = base64.b64decode(skill["image_b64"]); b = skill["body"]
    ps = pokeedge.PokeSkill(link)
    print(f"[4] skill '{skill_name}' from the store ({len(img)} B):", ps.load(img, b["limits"], b["axes"]))
    ps.attempt(skill["params"])
    cmd, ev = ps.step(2.5, 250, [0.0] * b["njoints"])
    print(f"      steps on POKE: after 2.5 s it asks for joints {[round(c, 2) for c in cmd]} rad "
          f"(stored params {[round(p, 2) for p in skill['params']]}), safety {ev}")
else:
    print(f"[4] no skill '{skill_name}' in the store")

info = json.loads(link.request(b"INFO", timeout=2))
print(f"\n✦ {time.time() - t0:.1f} s — resident {info.get('resident')}, touch {info.get('touch')}, build {info.get('build')}")
if twin:
    twin.__exit__(None, None, None)
