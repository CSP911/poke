#!/usr/bin/env python3
"""Build the POKE ontology — a RouteMind data repository — from what the
repository already knows: the library's device.json files, the design
documents, the skills, and the AI-incubation results.

    python3 tools/ontology.py [out_dir]        # default: ontology/repo

RouteMind reads a domain as areas that each advertise themselves in one
line (`use_when`); an agent picks an area from that list before reading
anything. Here the areas are the four kinds of knowledge a POKE hub needs
when a sentence arrives: PLATFORM (what the kernel offers and forbids),
DRIVERS (library residents and how to inject them), DEVICES (what is known
about particular chips — identification recipes and lessons), SKILLS
(motion skills, their ABI and safety rules). The output is regenerable;
regions.json is derived, as RouteMind requires.
"""
import os, re, sys, json, glob, textwrap, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "ontology", "repo")
LIB = os.path.join(ROOT, "edge", "library", "pi4")
INCUBATED = os.path.join(ROOT, "sim", "body", "build", "incubated")

nodes = []          # dicts: id, region, name, kind, one_liner, parent, body, role?, use_when?
edges = []          # (from, rel, to)

def add(region, id, name, kind, one_liner, body, parent=None, role=None, use_when=None):
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", id), id
    nodes.append(dict(region=region, id=id, name=name, kind=kind, one_liner=one_liner.strip(),
                      body=textwrap.dedent(body).strip() + "\n", parent=parent, role=role, use_when=use_when))

def edge(a, rel, b): edges.append((a, rel, b))

def slug(s): return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

# ── PLATFORM ───────────────────────────────────────────────────────────
add("platform", "platform", "POKE platform", "topic",
    "What the kernel offers to injected code and what it refuses to become",
    """
    # POKE platform
    The kernel is 37 KB and does four things: inject binaries, isolate them as EL0 processes, keep them
    within safety limits, and share time between them. Everything that knows what a device *is* lives
    outside it, in the library (DRIVERS) and is injected at run time. This area holds the contract a
    hub needs to generate code for the device, and the rules that keep the kernel from growing into an
    operating system.
    """, role="representative",
    use_when="which command injects what (EXLD/EXRN, PRST, RSLD, URUN, MLOD, SREQ) · what a unit may call (syscalls) · how a resident talks to the hub (service page) · whether something belongs in the kernel or in a resident · the QEMU twin and how it differs from the Pi · why the kernel must stay under 64 KB")

add("platform", "commands", "Hub commands", "protocol",
    "The UDP commands the kernel answers: staging, injection, process control, motion, services",
    """
    # Hub commands
    Every frame is `POKE` + len32 + payload; replies are `RESP` + len32 + data. Binaries larger than one
    datagram are staged in 1024-byte `EXLD` chunks (offset + data, acked "ok N"), then a finisher names
    the staged image by length and Fletcher-32 checksum.

    | command | what it does |
    |---|---|
    | `PING`, `INFO` | liveness; JSON with build, el, mmu, ram, pool, resident, persona, touch, last_log |
    | `EXLD` + `EXRN` | stage a flat aarch64 probe, run it once in kernel context (legacy, small probes) |
    | `URUN` | run a staged image as an EL0 unit; payload may carry capability mappings |
    | `PRST` / `PRUN` / `PSTP` | persona: stage, start (tick loop over api_t), stop |
    | `RSLD` / `RSTP` | resident driver: inject with mappings + name, stop (RES_STOP via sleep-return) |
    | `SREQ` | request to the resident through its service page; reply `SRSP` status + data |
    | `MLOD` / `MATT` / `MSTP` / `MSTO` | motion skill: load with body (joints, limits, axes), arm an attempt with 8 params, step once (returns filtered targets + safety counters), stop |
    | `KRLD` | replace the kernel over the network (trampoline at 0x00700000) |
    | `GPIO`, `TEMP`, `DRAW`, `TIME` | small direct services |
    """, parent="platform")

add("platform", "syscalls", "Unit syscalls", "protocol",
    "The ten primitives an EL0 unit may call; everything else is compiled into the unit",
    """
    # Unit syscalls (x8 = number, SVC #0)
    0 log(str) · 1 exit(code) · 2 yield · 3 sleep(ms) → returns 1 when the kernel wants a resident to
    stop · 4 mbox(buf, len) VideoCore mailbox on the unit's behalf (≤128 B) · 5 touch(x, y, tip)
    publish an input sample · 6 wait — park until the kernel resumes the unit on purpose (motion step) ·
    10–23 persona api_t slots (fb_clear, fb_rect, fb_text, now_ms, …).

    Conveniences are not syscalls: they live in headers compiled into the volatile binary
    (`skill_crt.c`, `i2c_bus.h`, `sdhci.h`). A unit never maps hardware it was not given.
    """, parent="platform")

add("platform", "service-page", "Resident service page", "protocol",
    "How the hub asks a resident for something: a 4 KB page, req_seq/rsp_seq, op + bytes",
    """
    # Resident service page
    The kernel maps one 4 KB page RW into every resident at `UNIT_SVC_VA` (0x100000190000). Layout
    (`svc_page_t` in poke_api.h): `req_seq, op, req_len, req[1536]` then `rsp_seq, status, rsp_len,
    rsp[1536]`. The hub sends `SREQ op data`; the kernel copies it in, bumps `req_seq`, and keeps
    resuming the resident until `rsp_seq == req_seq` (5 s limit). The resident answers in its TICK:
    fill `rsp`, `rsp_len`, `status`, `dsb sy`, then `rsp_seq = req_seq` last.

    Op numbering is the resident's own. Known: sdcard 1 info / 2 read(lba, n≤2) / 3 write(lba+512 B);
    i2c 1 scan / 2 read(addr, reg, n) / 3 write(addr, bytes); AI-written sensors 1 → JSON reading.
    """, parent="platform")

add("platform", "twin", "QEMU twin", "platform-rule",
    "The same kernel on a virtual Pi 4; what is identical and what differs from the hardware",
    """
    # QEMU twin
    `kernel8-qemu.img` is the kernel built with `-DNO_ETH`: the protocol rides the mini UART (QEMU
    serial 1), exposed as TCP by `pokeedge.Twin()`. EL1, MMU, EL0 units, scheduler and safety filter are
    identical to the hardware. Differences that matter:
    - the SD card sits on the older EMMC at 0xFE300000 (hardware: EMMC2 0xFE340000) — build residents with `-DEMMC_BASE`
    - one PL011 only: no UART2–5
    - I2C buses i2c-bus.0/1/2 = BSC0/1/2 at the hardware addresses; virtual devices via `-device`
    - the clock is host wall time, so the motion watchdog is 200 ms instead of 20 ms
    - the protocol socket accepts one connection; share the link
    - monitor (`qom-set t1 temperature 23500`) sets device state for ground truth
    """, parent="platform")

add("platform", "not-linux", "Why POKE must not become Linux", "platform-rule",
    "The contract and seven rules that keep the kernel a 64 KB substrate, not an operating system",
    open(os.path.join(ROOT, "docs", "design", "not-linux.md")).read(), parent="platform")

add("platform", "memory-check", "Hardware self-check lessons", "lesson",
    "What it took to test 4 GB of RAM at speed: caches on, SMPEN, code in cacheable memory, DMA regions excluded",
    """
    # Hardware self-check lessons
    `poke check memory` runs a parameter-patched probe over 64 MB chunks. It was 38 MB/s until three
    things were fixed: the MMU had to be on with caches; CPUECTLR_EL1.SMPEN had to be set or the caches
    stay off on an A72; and the probe's own code had to live in cacheable memory, because
    instruction fetch from a non-cacheable region was the bottleneck (28× once moved). Everything DMA
    touches — kernel 0–16 MB, GENET buffers, xHCI DMA, framebuffer — must be excluded and mapped
    non-cacheable, or the network dies silently (CPU reads stale cache while the chip wrote DRAM).
    `poke check edac` reads CPUMERRSR_EL1 / L2MERRSR_EL1 (A72 cache ECC); the Pi 4 DRAM has no ECC.
    """, parent="platform")

# ── DRIVERS (library residents) ────────────────────────────────────────
add("drivers", "drivers", "Library residents", "topic",
    "Injected drivers: what each one gives the hub, what windows it needs, and how it was verified",
    """
    # Library residents
    A resident is a flat binary (1–9 KB) injected with `RSLD` + capability mappings from its
    `device.json`. It runs as an EL0 process with only those windows, talks to the hub through its
    service page, and is stopped with `RSTP`. One resident slot exists today (SLOT_RESIDENT), so the SD
    driver steps aside for a device driver after what is needed was read. Nothing here is linked into
    the kernel.
    """, role="representative",
    use_when="which resident provides touch, block storage, an I2C bus, or a sensor reading · what windows a driver needs mapped · how to inject or stop one · what a driver's service ops are · which drivers were written by AI and where they were verified")

bus_nodes = {}
for dj_path in sorted(glob.glob(os.path.join(LIB, "*", "device.json"))):
    dj = json.load(open(dj_path)); name = dj["name"]; nid = slug(name)
    maps = "\n".join(f"- `{m['pa']}` len `{m['len']}` {m['attr']}" + (f" — {m['what']}" if m.get("what") else "") for m in dj.get("mappings", []))
    inc = dj.get("incubated")
    body = f"""
    # {name}
    {dj.get('description', '')}

    **Provides:** {', '.join(dj.get('provides', []))} · **inject:** `poke resident {name}` · **source:** `{dj.get('source')}`

    ## Capability windows
    {maps or '- none'}
    """
    if dj.get("service"):
        body += "\n## Service\n" + "\n".join(f"- {k}: `{v}`" for k, v in dj["service"].items()) + "\n"
    if inc:
        body += f"\n## Provenance\nWritten by **{inc.get('by')}** during AI incubation on {inc.get('at')}, verified on **{inc.get('verified_on')}** in {inc.get('rounds')} round(s). Evidence from identification: {dj.get('evidence', '')}\n"
    if dj.get("hardware"):
        body += "\n## Hardware notes\n" + "\n".join(f"- **{k}**: {v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}" for k, v in dj["hardware"].items()) + "\n"
    one = (dj.get("description", "").split(".")[0] + ".")[:200]
    add("drivers", nid, name, "driver", one, body, parent="drivers")
    for i, l in enumerate(dj.get("lessons", [])):
        add("drivers", f"{nid}-lesson-{i+1}", f"{name}: lesson {i+1}", "lesson", l[:200], f"# {name} — lesson {i+1}\n{l}\n", parent=nid)
        edge(f"{nid}-lesson-{i+1}", "LEARNED_FROM", nid)
    if "i2c" in dj.get("provides", []): bus_nodes["i2c"] = nid
    if "block" in dj.get("provides", []): bus_nodes["block"] = nid
    edge(nid, "VERIFIED_ON", "twin" if (inc and inc.get("verified_on") == "twin") else "pi4-hardware")
for dj_path in sorted(glob.glob(os.path.join(LIB, "*", "device.json"))):
    dj = json.load(open(dj_path)); nid = slug(dj["name"])
    if nid != bus_nodes.get("i2c") and any("0xFE804000" == m["pa"] for m in dj.get("mappings", [])) and bus_nodes.get("i2c"):
        edge(nid, "USES_BUS", bus_nodes["i2c"])

add("platform", "pi4-hardware", "Raspberry Pi 4 (BCM2711)", "device",
    "The physical edge: Cortex-A72 ×4, 4 GB, GENET ethernet, EMMC2 SD, BSC I2C, PCIe → VL805 USB",
    """
    # Raspberry Pi 4
    Boots the kernel from the SD's FAT partition (the SD kernel is older; `poke kernel` reloads over
    the network). Hub link: GENET ethernet, 10.0.0.2. Windows the drivers use: EMMC2 0xFE340000,
    BSC1 0xFE804000 (GPIO 2/3), GPIO 0xFE200000, PCIe host bridge 0xFD500000, xHCI window 0x600000000.
    Memory layout: kernel 0–16 MB, GENET buffers 0x02000000, xHCI DMA 0x02200000, page pool from
    0x40000000. The SD's second partition (14.1 GB) is the POKE store.
    """, parent="platform")
edge("twin", "TWIN_OF", "pi4-hardware")

# ── DEVICES (chip knowledge, identification recipes) ───────────────────
add("devices", "devices", "Devices and identification", "topic",
    "What is known about particular chips: how to recognise one on a bus and how to read it",
    """
    # Devices and identification
    Knowledge about chips, separate from the drivers that use them: identification registers, value
    formats, quirks of the QEMU models, and the recipes an incubation produced. When something unknown
    answers on a bus, this is where to look before probing blind.
    """, role="representative",
    use_when="an unknown I2C address answered and you want to know what it probably is · how to read a temperature / time / magnetic field from a known part · ID registers and value formats (TMP10x, DS1307/1338, HMC5883/LSM303, PCA9552, eGalax touch, VL805 hub) · which QEMU device models misbehave")

# A map an agent trusts passes its errors on silently (RouteMind's one miss in 700 was a wrong
# sentence in the map). So an identification is advertised as a *recipe* only when a driver written
# from it was verified against ground truth (stage 2); anything else is an *open question* that states
# what was observed and what was hypothesised, and says plainly that nothing confirmed it.
verified = {}
for dj_path in glob.glob(os.path.join(LIB, "*", "device.json")):
    dj = json.load(open(dj_path))
    m = re.search(r"-([0-9a-f]{2})$", dj["name"])          # library names end in the address: i2c-temp-49
    if dj.get("incubated") and m: verified[int(m.group(1), 16)] = dj
for f in sorted(glob.glob(os.path.join(INCUBATED, "0x*.json"))):
    d = json.load(open(f)); a = d["addr"] if isinstance(d["addr"], int) else int(str(d["addr"]), 16)
    nid = f"i2c-0x{a:02x}"
    if a in verified:
        v = verified[a]
        add("devices", nid, f"I2C 0x{a:02x}: {d['identity'][:60]}", "recipe",
            f"At 0x{a:02x}: {d['identity'][:110]} — VERIFIED: a driver written from this reads correct values on the {v['incubated']['verified_on']}",
            f"""
            # I2C 0x{a:02x} — {d['identity']}
            **Status: verified.** The resident `{v['name']}` was written from this identification and its readings
            matched ground truth on the {v['incubated']['verified_on']} ({v['incubated']['at']}).

            **Evidence:** {d.get('evidence', '')}

            **Reading:** {d.get('read_recipe', d.get('main_value', '—'))}
            """, parent="devices")
        edge(nid, "DRIVEN_BY", slug(v["name"]))
    else:
        add("devices", nid, f"I2C 0x{a:02x}: unresolved", "lesson",
            f"At 0x{a:02x}: OPEN QUESTION — an earlier guess ({d['identity'][:60]}, {float(d.get('confidence', 0)):.0%}) was never confirmed; do not inherit it",
            f"""
            # I2C 0x{a:02x} — unresolved
            **Status: not identified.** An earlier incubation guessed *{d['identity']}* at {float(d.get('confidence', 0)):.0%}
            confidence and no driver was ever verified from it. Treat that guess as one hypothesis among others, not as knowledge.

            **What was observed on the bus:** {d.get('evidence', '')}

            **How to settle it:** form two or more hypotheses from the address and the observed bytes, pick the register reads that would
            tell them apart, and only conclude when a reading matches one part's documented register map and not the others'. If you
            settle it, say which read decided it.
            """, parent="devices")
    edge(nid, "FOUND_ON", bus_nodes.get("i2c", "i2c"))

add("devices", "egalax-touch", "eGalax USB touch (0eef:0005)", "device",
    "Full-speed HID touch on the VL805 hub: 11-byte reports, X 0..1023, Y 0..599, ~130 Hz",
    """
    # eGalax touch controller
    VID 0x0eef PID 0x0005, full speed, EP0 max packet 64 (despite full speed — learn it from the first
    8 descriptor bytes), interrupt IN 0x82, 11-byte reports: [1] bit0 tip, [4..5] X LE 0..1023,
    [6..7] Y LE 0..599 in panel pixels. Never SET_IDLE: it STALLs and wedges the hub's single TT.
    Behind the VL805's internal hub 2109:3431 (4 ports, single TT → child TT port number 0).
    """, parent="devices")
edge("egalax-touch", "DRIVEN_BY", "usb-touch-egalax")

add("devices", "qemu-models", "QEMU device model quirks", "lesson",
    "How QEMU's I2C, SD and sensor models differ from silicon, and which ones crash",
    """
    # QEMU device model quirks
    - BSC (bcm2835_i2c): begins a transfer on *any* C write with I2CEN set → write C once per transfer, after A and DLEN; write data after TA; NAK = ERR without DONE; zero-length transfers never finish (scan with 1-byte reads)
    - SD: the card is on the older EMMC at 0xFE300000; CSD v1 reports 64 MB for the test image; identification needs 0 polls
    - tmp105: default 9-bit resolution (0.5 °C steps); `qom-set <id> temperature <milli-°C>`
    - lsm303dlhc_mag: `mag-x/y/z` in field units where 100000 reads 1100 counts (1 G); refuses negative values; Z scaled by 980/1100
    - ds1338: follows the host clock in UTC
    - at24c-eeprom: probing it crashes QEMU — do not attach it
    - pca9552: input registers read 0xFF, LS registers 0x55; still misidentified by the model (open)
    """, parent="devices")
edge("qemu-models", "LEARNED_FROM", "twin")

# ── SKILLS ─────────────────────────────────────────────────────────────
add("skills", "skills", "Motion skills", "topic",
    "Generated movement programs: the ABI they speak, the safety they run under, and the ones that worked",
    """
    # Motion skills
    A skill is a volatile C program generated for one body and one intent. It speaks motion_io.h,
    runs as an EL0 unit stepped by `MSTP`, and only ever writes joint targets into its own page; the
    kernel's safety filter decides what reaches a motor. Skills that a person approved ("그거야") are
    remembered on the device's SD with their tuned numbers.
    """, role="representative",
    use_when="how to write a skill for a body (the motion ABI) · which skills worked on a 3-joint finger or a servo chain, with their tuned params · what the safety filter clamps · how skills are tuned from a person's voice · how a skill is stored and restored")

add("skills", "motion-abi", "Motion ABI (motion_io.h)", "protocol",
    "The struct a skill reads and writes each tick: body, state, params in; cmd out",
    "# Motion ABI\n```c\n" + open(os.path.join(ROOT, "edge", "kernel", "pi4", "motion_io.h")).read() + "```\n"
    "Skills are compiled with `edge/library/pi4/skillrt` (a crt that parks in SYS_WAIT and calls skill_step per tick, and a libm-free math.h). Expose uncertain numbers as `io->param[i]` with ranges in a `// SKILL-META` first line; a tuner searches them on the body.\n",
    parent="skills")

add("skills", "motion-safety", "Motion safety rules", "platform-rule",
    "Angle limit, 6 rad/s rate limit, tilt e-stop, NaN hold, watchdog — enforced by the kernel, not the skill",
    open(os.path.join(ROOT, "docs", "design", "motion-safety.md")).read(), parent="skills")

for src, nid, name, body_desc in (
        (os.path.join(ROOT, "sim", "body", "skills", "gen-coach-2.c"), "finger-fist", "Finger fist (3 pitch joints)", "a finger of three servo joints on a fixed palm; negative angles curl toward the palm"),
        (os.path.join(ROOT, "sim", "body", "skills", "gen-a2.c"), "crawl-4", "Crawl, 4-joint chain", "a chain of five segments and four pitch joints lying on the ground, head first (+x)"),
        (os.path.join(ROOT, "sim", "body", "skills", "gen-b2.c"), "crawl-3", "Crawl, 3-joint chain", "a chain of four segments and three pitch joints lying on the ground")):
    if not os.path.exists(src): continue
    code = open(src).read(); m = re.search(r"SKILL-META\s+(\{.*\})", code.split("\n")[0])
    meta = json.loads(m.group(1)) if m else {}
    add("skills", nid, name, "skill",
        f"{meta.get('name', nid)}: {meta.get('idea', '')[:160]}",
        f"""
        # {name}
        **Body:** {body_desc}

        **Idea:** {meta.get('idea', '')}

        **Params:** {', '.join(f"{p['name']} [{p['lo']}, {p['hi']}]" for p in meta.get('params', []))}

        ```c
        {code}
        ```
        """, parent="skills")
    edge(nid, "SPEAKS", "motion-abi")

add("skills", "coaching", "Voice coaching", "procedure",
    "A person watches and talks; comments score attempts, name parts, stop, or declare done",
    """
    # Voice coaching (sim/body/coach.py)
    The person's words are the reward: "쥐었어" +2, "말았어" +1, "움직였어" +0.5, "풀렸어/반대야" −1.5.
    "멈춰" freezes at once without the model. "그거야" saves the skill, its tuned numbers and its POKE
    image to the device's SD (edge store). A comment that names a part ("끝마디도 더 굽혀") pushes only
    the params named after that part. Fast loop: explore around praised params. Slow loop: when praise
    stalls, the model rewrites the structure from the transcript. Speech lags what it describes by
    about 0.8 s; utterances are matched to the attempt a moment earlier. On the twin a stand-in
    watcher with ground truth plays the person.
    """, parent="skills")

# ── write the repository ───────────────────────────────────────────────
AREAS = [("platform", "PLATFORM", "POKE platform", "What the kernel offers to injected code, the twin, and the rules that keep it small"),
         ("drivers", "DRIVERS", "Library residents", "Injected drivers, their windows, service ops and provenance"),
         ("devices", "DEVICES", "Devices and identification", "Chip knowledge: how to recognise and read particular parts, QEMU quirks"),
         ("skills", "SKILLS", "Motion skills", "Generated movement programs, their ABI, safety and coaching")]

os.makedirs(OUT, exist_ok=True)
for d in glob.glob(os.path.join(OUT, "regions", "*")):
    for f in glob.glob(os.path.join(d, "*.md")): os.remove(f)
vocab = {
  "budgets": {"kinds": 12, "entities": 400, "children_per_entity": 60, "relations_per_group": 8, "edge_note_max_lines": 6},
  "default_kind": "topic",
  "kinds": [
    {"id": "topic", "desc": "a place that only groups what is under it"},
    {"id": "protocol", "desc": "a wire or memory contract both sides must honour — commands, syscalls, a struct layout"},
    {"id": "platform-rule", "desc": "a rule about the kernel or the platform itself — what it must and must not do"},
    {"id": "driver", "desc": "a library resident: an injected binary that owns one piece of hardware"},
    {"id": "device", "desc": "a chip or board — what it is, its addresses and registers"},
    {"id": "recipe", "desc": "how to recognise a part on a bus and read its main value"},
    {"id": "lesson", "desc": "something learned the hard way while bringing hardware up — a trap and its fix"},
    {"id": "skill", "desc": "a generated movement program for one body and intent, with its tuned params"},
    {"id": "procedure", "desc": "an ordered way of doing something on the hub side"}],
  "relations": [
    {"group": "hardware", "rels": [
      {"id": "DRIVEN_BY", "template": "{from} is driven by {to}", "inverse": "{to} drives {from}", "desc": "which resident owns this device"},
      {"id": "USES_BUS", "template": "{from} uses the bus {to}", "inverse": "{to} carries {from}", "desc": "which bus service a driver depends on"},
      {"id": "FOUND_ON", "template": "{from} was found on {to}", "inverse": "{to} has {from} attached", "desc": "where a device answered"}]},
    {"group": "provenance", "rels": [
      {"id": "VERIFIED_ON", "template": "{from} was verified on {to}", "inverse": "{to} verified {from}", "desc": "twin or hardware"},
      {"id": "LEARNED_FROM", "template": "{from} was learned from {to}", "inverse": "{to} taught {from}", "desc": "where a lesson came from"},
      {"id": "TWIN_OF", "template": "{from} is the twin of {to}", "inverse": "{to} has the twin {from}", "desc": "virtual stand-in"}]},
    {"group": "motion", "rels": [
      {"id": "SPEAKS", "template": "{from} speaks {to}", "inverse": "{to} is spoken by {from}", "desc": "which ABI a skill is written against"}]}],
  "edge_rules": []}
def yaml_dump(o, ind=0):
    sp = "  " * ind
    if isinstance(o, dict):
        out = ""
        for k, v in o.items():
            if isinstance(v, (dict, list)) and v: out += f"{sp}{k}:\n{yaml_dump(v, ind + 1)}"
            else: out += f"{sp}{k}: {json.dumps(v, ensure_ascii=False) if not isinstance(v, (int, float)) or isinstance(v, bool) else v}\n"
        return out
    if isinstance(o, list):
        out = ""
        for v in o:
            if isinstance(v, dict):
                first = True
                for k, vv in v.items():
                    pre = f"{sp}- " if first else f"{sp}  "
                    if isinstance(vv, (dict, list)) and vv: out += f"{pre}{k}:\n{yaml_dump(vv, ind + 2)}"
                    else: out += f"{pre}{k}: {json.dumps(vv, ensure_ascii=False) if not isinstance(vv, (int, float)) or isinstance(vv, bool) else vv}\n"
                    first = False
            else: out += f"{sp}- {json.dumps(v, ensure_ascii=False)}\n"
        return out
    return f"{sp}{json.dumps(o, ensure_ascii=False)}\n"
open(os.path.join(OUT, "vocab.yaml"), "w").write("# The vocabulary of the POKE ontology — generated by tools/ontology.py\n" + yaml_dump(vocab))

core = """# Core

POKE (Protocol for Open Kernel Execution): a hub turns a sentence into a small binary and injects it
into an OS-less device, where a 37 KB kernel runs it as an isolated process under safety rules.
Software's unit is the sentence; the binary is its pronunciation for this body and this moment, and
is thrown away. What is kept is knowledge — and this ontology is that knowledge, cut by **what a hub
needs when a sentence arrives**: the platform's contract, the drivers it can inject, what is known
about chips, and the movement skills that worked.

## What hangs on what

**A sentence becomes a binary only through the platform's contract.** Whatever is generated must be
flat aarch64, speak the syscalls, and fit the windows it is given — PLATFORM says what those are.

**Drivers are injected, never built in.** A hub that needs touch, storage or a bus looks in DRIVERS
for a resident and its `device.json` mappings; the kernel maps exactly those windows.

**Unknown hardware is incubated, and what was learned goes to DEVICES.** Identification recipes and
QEMU quirks live there so the next incubation reads before it probes. A verified driver that came out
of an incubation is listed in DRIVERS with its provenance.

**Skills are verified on a body and remembered.** SKILLS holds the ABI, the safety rules the kernel
enforces regardless of what a skill asks, and the skills a person approved with their tuned numbers.

## Areas

| Area | What it holds |
|---|---|
""" + "\n".join(f"| `{A}` | {desc} |" for _, A, _, desc in AREAS) + f"\n\nGenerated {datetime.date.today()} by tools/ontology.py from edge/library, docs/design, sim/body.\n"
open(os.path.join(OUT, "CORE.md"), "w").write(core)

for n in nodes:
    d = os.path.join(OUT, "regions", n["region"]); os.makedirs(d, exist_ok=True)
    fm = [f"id: {n['id']}", f'name: {json.dumps(n["name"], ensure_ascii=False)}', f"kind: {n['kind']}",
          f'one_liner: {json.dumps(n["one_liner"], ensure_ascii=False)}', "injected_by: tools/ontology.py"]
    if n["parent"]: fm.append(f"parent: {n['parent']}")
    if n["role"]: fm.append(f"role: {n['role']}")
    if n["use_when"]: fm.append(f'use_when: {json.dumps(n["use_when"], ensure_ascii=False)}')
    open(os.path.join(d, f"{n['id']}.md"), "w").write("---\n" + "\n".join(fm) + "\n---\n" + n["body"])
ids = {n["id"] for n in nodes}
open(os.path.join(OUT, "edges.yaml"), "w").write("# Edges — generated by tools/ontology.py\n\n" +
    "".join(f"- from: {a}\n  rel: {r}\n  to: {b}\n" for a, r, b in edges if a in ids and b in ids))
regions = []
for src, A, title, desc in AREAS:
    rep = next(n for n in nodes if n["region"] == src and n["role"] == "representative")
    regions.append({"id": A, "source": src, "title": title, "description": desc, "use_when": rep["use_when"],
                    "export": False, "export_to": [], "nodes": sorted(n["id"] for n in nodes if n["region"] == src),
                    "representative": rep["id"], "fetch": f"/v1/regions/{src}"})
json.dump({"schema": "iris-ontology-regions/v2", "regions": regions}, open(os.path.join(OUT, "regions.json"), "w"), indent=1, ensure_ascii=False)
dropped = [(a, r, b) for a, r, b in edges if a not in ids or b not in ids]
print(f"{OUT}: {len(nodes)} nodes in {len(AREAS)} areas, {len(edges) - len(dropped)} edges" + (f" ({len(dropped)} dropped: {dropped})" if dropped else ""))
