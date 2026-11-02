#!/usr/bin/env python3
"""Pre-flight for a real Pi 4 sentence boot: everything the hub can check before the first netboot.

    ../sim/body/.venv/bin/python netboot/preflight.py [IMAGE] [--tftp-ip 10.0.0.1] [--json]

Checks (FAIL blocks a first boot, WARN does not):
  serial      a USB-serial adapter is attached (the node reports on GPIO 14 → adapter RX)
  network     an interface carries the TFTP server address the Pi's EEPROM points at (TFTP_IP)
  tftp        UDP 69 can be bound, or netboot/tftpd.py already serves it
  rivals      no virtual bootloader (vboot.py) is consuming netboot/root, no stale pending.img
  firmware    the start/fixup pair config.txt selects is in netboot/root (gpu_mem=16 → start4cd/fixup4cd)
  config      config.txt keeps what the image and the hub assume (64-bit, kernel=sentence.img at 0x80000,
              UART on, core clock pinned to the clock rtcfg's Pi CDIV is computed from)
  image       (IMAGE given) has an RCFG block, the Pi settings fit its rules, size fits the fence's 2 MB
  eeprom      prints the bootloader settings to apply once on the Pi (teams/hw/eeprom.conf); not checkable here
Exit 0 when nothing FAILs.
"""
import os, re, sys, glob, json, socket, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
SENT = os.path.dirname(HERE)
sys.path.insert(0, SENT)
import rtcfg

ROOT = os.path.join(HERE, "root")
EEPROM = os.path.join(SENT, "teams", "hw", "eeprom.conf")
rows = []

def res(name, level, msg):
    rows.append({"check": name, "level": level, "msg": msg})

def arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default

def cfg_lines(path):
    out = {}
    for l in open(path).read().splitlines():
        l = l.split("#", 1)[0].strip()
        if "=" in l:
            k, v = l.split("=", 1); out[k.strip()] = v.strip()
    return out

def check_serial():
    devs = sorted(set(glob.glob("/dev/cu.usbserial*") + glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.SLAB_USBtoUART*")
                      + glob.glob("/dev/cu.wchusbserial*")))
    if not devs:
        res("serial", "FAIL", "no USB-serial adapter (/dev/cu.usbserial*, usbmodem*, SLAB*, wchusbserial*). "
                              "Wire Pi GPIO14 (pin 8) → adapter RX, GND (pin 6) → GND; 3.3 V adapter only")
    elif len(devs) > 1:
        res("serial", "WARN", f"several serial devices {devs}: say.py picks the first — pass the right one explicitly")
    else:
        res("serial", "OK", devs[0])

def check_network(tftp_ip):
    out = subprocess.run(["ifconfig"], capture_output=True, text=True).stdout
    iface, found = None, None
    for l in out.splitlines():
        if l and not l[0].isspace(): iface = l.split(":")[0]
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", l)
        if m and m.group(1) == tftp_ip: found = iface
    if found:
        st = "active" in subprocess.run(["ifconfig", found], capture_output=True, text=True).stdout
        res("network", "OK" if st else "WARN", f"{tftp_ip} on {found}" + ("" if st else " — link not active (cable / Pi off?)"))
    else:
        res("network", "FAIL", f"no interface has {tftp_ip} (the Pi's TFTP_IP). Connect the Pi by Ethernet and set the adapter "
                               f"to manual {tftp_ip}/255.255.255.0 (System Settings → Network), no router needed")

def check_tftp():
    running = subprocess.run(["pgrep", "-f", "netboot/tftpd.py"], capture_output=True, text=True).stdout.split()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("0.0.0.0", 69)); s.close()
        res("tftp", "OK", "UDP 69 free — start the server: ../sim/body/.venv/bin/python netboot/tftpd.py")
    except OSError as e:
        if running:
            res("tftp", "OK", f"netboot/tftpd.py is serving (pid {', '.join(running)})")
        else:
            res("tftp", "FAIL", f"UDP 69 busy and not by netboot/tftpd.py ({e}) — macOS tftpd? `sudo launchctl unload -w "
                                "/System/Library/LaunchDaemons/tftp.plist`")

def check_rivals():
    vb = subprocess.run(["pgrep", "-f", "vboot.py"], capture_output=True, text=True).stdout.split()
    if vb:
        res("rivals", "FAIL", f"vboot.py (virtual bootloader, pid {', '.join(vb)}) is running — it would take the Pi's sentence "
                              "from the same netboot/root. Stop it first")
    elif os.path.exists(os.path.join(ROOT, "pending.img")):
        res("rivals", "WARN", "netboot/root/pending.img exists — a sentence nobody fetched; the Pi would boot it next. Remove it if stale")
    else:
        res("rivals", "OK", "no virtual bootloader, nothing pending")

def vc_build(path):
    try:
        s = open(path, "rb").read()
        m = re.search(rb"VC_BUILD_ID_TIME: ([A-Z][a-z]{2} +\d+ \d{4})", s)
        return m.group(1).decode() if m else "?"
    except OSError:
        return None

def check_firmware(cfg):
    cd = cfg.get("gpu_mem") == "16"
    start, fixup = ("start4cd.elf", "fixup4cd.dat") if cd else ("start4.elf", "fixup4.dat")
    missing = [f for f in (start, fixup, "bcm2711-rpi-4-b.dtb") if not os.path.exists(os.path.join(ROOT, f))]
    if missing:
        res("firmware", "FAIL", f"missing in netboot/root: {missing} (config.txt {'gpu_mem=16 → cut-down pair' if cd else 'standard pair'})")
        return
    sz = {f: os.path.getsize(os.path.join(ROOT, f)) for f in (start, fixup)}
    res("firmware", "OK", f"{start} {sz[start]} B (VideoCore build {vc_build(os.path.join(ROOT, start))}) + {fixup} {sz[fixup]} B "
                          f"— TFTP ≈ {(sz[start] + 1467) // 1468} blocks of 1468 B")
    if cd:
        res("firmware", "WARN", "first hardware boot: the cut-down pair is untested on this Pi; if it does not start, drop gpu_mem=16 "
                                "(standard start4.elf/fixup4.dat) and compare")

def check_config(cfg):
    want = {"arm_64bit": "1", "kernel": "sentence.img", "enable_uart": "1"}
    bad = [f"{k}={cfg.get(k)} (want {v})" for k, v in want.items() if cfg.get(k) != v]
    ka = cfg.get("kernel_address")
    if ka and int(ka, 0) != 0x80000: bad.append(f"kernel_address={ka} (the image is linked at 0x80000)")
    pi_core = rtcfg.CORE_HZ["pi"] // 1_000_000
    for k in ("core_freq", "core_freq_min"):
        if cfg.get(k) != str(pi_core):
            bad.append(f"{k}={cfg.get(k)} (rtcfg's Pi CDIV {rtcfg.TARGETS['pi']['cdiv']} assumes a core clock pinned at {pi_core} MHz)")
    if bad: res("config", "FAIL", "; ".join(bad))
    else: res("config", "OK", f"64-bit, kernel=sentence.img @0x80000, UART on, core clock pinned {pi_core} MHz")
    if cfg.get("uart_2ndstage") != "1":
        res("config", "WARN", "first hardware boot: add uart_2ndstage=1 (firmware log on the same wire) until the node reports")

def check_image(path):
    data = open(path, "rb").read()
    try:
        c = rtcfg.read(data)
    except rtcfg.RcfgError as e:
        res("image", "FAIL", f"{os.path.basename(path)}: {e}"); return
    try:
        pi = rtcfg.read(rtcfg.for_target(data, "pi"))
    except rtcfg.RcfgError as e:
        res("image", "FAIL", f"Pi settings do not fit: {e}"); return
    if len(data) > 0x200000 - 0x80000:
        res("image", "FAIL", f"{len(data)} B: past the 2 MB the fence maps"); return
    res("image", "OK", f"{os.path.basename(path)} {len(data)} B; RCFG as built {dict((k, c[k]) for k in ('wdog', 'cdiv', 'budget_ms'))}"
                       f" → for the Pi {dict((k, pi[k]) for k in ('wdog', 'cdiv', 'quiet_ms', 'budget_ms'))} (tftpd --pend IMG --target pi)")

def main():
    tftp_ip = arg("--tftp-ip", "10.0.0.1")
    check_serial(); check_network(tftp_ip); check_tftp(); check_rivals()
    cpath = os.path.join(ROOT, "config.txt")
    cfg = cfg_lines(cpath) if os.path.exists(cpath) else {}
    if not cfg: res("config", "FAIL", "netboot/root/config.txt missing")
    else: check_firmware(cfg); check_config(cfg)
    img = next((a for a in sys.argv[1:] if a.endswith(".img")), None)
    if img: check_image(img)
    res("eeprom", "INFO", f"once, on the Pi (Raspberry Pi OS, `sudo rpi-eeprom-config --edit`): {EEPROM}")
    if "--json" in sys.argv:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        for r in rows: print(f"{r['level']:5} {r['check']:9} {r['msg']}")
    fails = [r for r in rows if r["level"] == "FAIL"]
    print(f"\npreflight: {'READY' if not fails else str(len(fails)) + ' to fix: ' + ', '.join(r['check'] for r in fails)}")
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
