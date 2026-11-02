#!/usr/bin/env python3
"""seal — only images the hub approved boot on the node: the Raspberry Pi 4's secure-boot format.

The node keeps nothing of ours, so everything it boots comes over the network, and TFTP has no
authentication: whoever answers first decides what the node becomes. "The night image has no gate code"
is only true if the node refuses an image the hub did not make. The Pi 4's own bootloader (the
manufacturer's EEPROM firmware, not our code) can do that check, so the hub speaks its format:

  boot.img   a FAT16 ramdisk holding what the firmware reads: config.txt, the VideoCore firmware pair,
             the device tree, sentence.img (our image) and poke.json (what this image is, for the record)
  boot.sig   "<sha256 of boot.img>\\nts: <unix time>\\nrsa2048: <hex>\\n" — RSA-2048 PKCS#1 v1.5 over
             SHA-256 of boot.img, signed with the hub's key (the format rpi-eeprom-digest writes)

A real Pi checks it when its EEPROM has SIGNED_BOOT=1 and the hub's public key (rpi-eeprom-config -p);
see netboot/README.md "Signed boot". On the twin, netboot/vboot.py --signed does the same check. The
verifier here is plain Python (no openssl), the way the bootloader holds only the public key.

A signature says who, not when: before it signs, the hub stamps its public key into the image (stamp, the
TKEY block), and the image's runtime then asks the hub for a ticket on a fresh nonce before its op runs
(edge/sentence/rt_fresh.h; the hub's desk is netboot/tftpd.py). A replayed image gets no ticket.

The hub's private key stays on the hub: POKE_KEYS (a directory) or sentence/keys/ (git-ignored, 0600).
Signing uses the openssl command; it is created on first use.

    seal.py key                          the key's paths and fingerprint (creates it if missing)
    seal.py pack IMAGE [-o DIR] [--target pi]   → DIR/boot.img, DIR/boot.sig
    seal.py verify BOOT.IMG BOOT.SIG [PUBKEY]
    seal.py list BOOT.IMG
"""
import os, sys, json, time, base64, hashlib, struct, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "netboot", "root")
FIRMWARE = ("start4cd.elf", "fixup4cd.dat", "start4.elf", "fixup4.dat", "bcm2711-rpi-4-b.dtb")
KERNEL = "sentence.img"

# ── the hub's key ───────────────────────────────────────────────────────
def keys_dir():
    return os.environ.get("POKE_KEYS") or os.path.join(HERE, "keys")

def key_path():
    return os.path.join(keys_dir(), "hub-rsa2048.pem")

def pub_path():
    return os.path.join(keys_dir(), "hub-rsa2048.pub.pem")

def ensure_key():
    """The hub's signing key: created once, never leaves the hub. Returns (private, public) paths."""
    k, p = key_path(), pub_path()
    if not os.path.exists(k):
        os.makedirs(keys_dir(), mode=0o700, exist_ok=True)
        tmp = k + f".{os.getpid()}.tmp"
        old = os.umask(0o077)
        try:
            subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", tmp],
                           check=True, capture_output=True)
        finally:
            os.umask(old)
        try: os.link(tmp, k)                         # first writer wins when two hubs race
        except FileExistsError: pass
        os.remove(tmp)
    if not os.path.exists(p):
        pem = subprocess.run(["openssl", "pkey", "-in", k, "-pubout"], check=True, capture_output=True).stdout
        tmp = p + f".{os.getpid()}.tmp"; open(tmp, "wb").write(pem); os.replace(tmp, p)
    return k, p

def sign(data, key=None):
    """RSA-2048 PKCS#1 v1.5 over SHA-256 of data, by the hub's key."""
    key = key or ensure_key()[0]
    r = subprocess.run(["openssl", "dgst", "-sha256", "-sign", key], input=data, capture_output=True, check=True)
    return r.stdout

def boot_sig(boot_img, key=None, ts=None):
    """The boot.sig text for boot.img (rpi-eeprom-digest's format)."""
    return (f"{hashlib.sha256(boot_img).hexdigest()}\n"
            f"ts: {int(ts if ts is not None else time.time())}\n"
            f"rsa2048: {sign(boot_img, key).hex()}\n")

# ── verification: what the bootloader does, with only the public key ────
def _der(b, i):
    """One DER TLV at i → (tag, value bytes, next index)."""
    tag, n = b[i], b[i + 1]; i += 2
    if n & 0x80:
        k = n & 0x7F; n = int.from_bytes(b[i:i + k], "big"); i += k
    return tag, b[i:i + n], i + n

def public_key(pem):
    """PEM (SubjectPublicKeyInfo or PKCS#1 RSAPublicKey; a path or the text) → (n, e)."""
    if isinstance(pem, str) and "-----" not in pem and os.path.exists(pem):
        pem = open(pem, "rb").read()
    if isinstance(pem, bytes):
        pem = pem.decode()
    body = "".join(l for l in pem.splitlines() if l and not l.startswith("-----"))
    der = base64.b64decode(body)
    _, seq, _ = _der(der, 0)
    tag, v, j = _der(seq, 0)
    if tag == 0x30:                                   # SubjectPublicKeyInfo: algorithm, BIT STRING(RSAPublicKey)
        _, bits, _ = _der(seq, j)
        _, seq, _ = _der(bits[1:], 0)
        tag, v, j = _der(seq, 0)
    n = int.from_bytes(v, "big")
    _, ev, _ = _der(seq, j)
    return n, int.from_bytes(ev, "big")

SHA256_INFO = bytes.fromhex("3031300d060960864801650304020105000420")

def parse_sig(text):
    """boot.sig text → {"sha256", "ts", "rsa2048"(bytes)}; missing fields are absent."""
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    out, lines = {}, [l.strip() for l in text.splitlines() if l.strip()]
    if lines and ":" not in lines[0]:
        out["sha256"] = lines[0].lower()
    for l in lines:
        k, _, v = l.partition(":")
        if k.strip() == "ts": out["ts"] = int(v.strip()) if v.strip().isdigit() else None
        if k.strip() == "rsa2048":
            try: out["rsa2048"] = bytes.fromhex(v.strip())
            except ValueError: out["rsa2048"] = b""
    return out

def verify(boot_img, sig_text, pub):
    """The bootloader's check: boot.sig names this boot.img's hash and carries a valid RSA-2048 signature by
    the trusted key. → (True, "ok") or (False, why)."""
    if not sig_text:
        return False, "no boot.sig"
    s = parse_sig(sig_text)
    digest = hashlib.sha256(boot_img).digest()
    if s.get("sha256") != digest.hex():
        return False, "boot.sig names a different boot.img (hash mismatch)"
    sig = s.get("rsa2048") or b""
    n, e = public_key(pub)
    k = (n.bit_length() + 7) // 8
    if len(sig) != k:
        return False, "no RSA-2048 signature in boot.sig"
    m = pow(int.from_bytes(sig, "big"), e, n).to_bytes(k, "big")
    want = b"\x00\x01" + b"\xff" * (k - 3 - len(SHA256_INFO) - 32) + b"\x00" + SHA256_INFO + digest
    if m != want:
        return False, "signature is not by the trusted key"
    return True, "ok"

def fingerprint(pub):
    n, e = public_key(pub)
    return hashlib.sha256(n.to_bytes((n.bit_length() + 7) // 8, "big") + e.to_bytes(4, "big")).hexdigest()[:16]

# ── FAT16 ramdisk (boot.img) ────────────────────────────────────────────
SEC, ROOT_ENTRIES, MIN_CLUSTERS = 512, 512, 4100      # one sector per cluster; ≥ 4085 clusters makes it FAT16
FIXED_DATE, FIXED_TIME = ((2026 - 1980) << 9) | (1 << 5) | 1, 0   # every entry dated 2026-01-01 00:00 → same files, same bytes

def _short(name, used):
    base, _, ext = name.upper().rpartition(".")
    if not base: base, ext = ext, ""
    ok = lambda s: all(c.isascii() and (c.isalnum() or c in "_-") for c in s)
    if 0 < len(base) <= 8 and len(ext) <= 3 and ok(base) and ok(ext):      # FAT is case-insensitive: CONFIG.TXT is config.txt
        return base.ljust(8).encode() + ext.ljust(3).encode(), False
    stem = "".join(c for c in base if c.isalnum())[:6] or "FILE"
    for i in range(1, 100):
        s = (stem + f"~{i}").ljust(8)[:8].encode() + "".join(c for c in ext if c.isalnum())[:3].ljust(3).encode()
        if s not in used:
            return s, True
    raise ValueError(name)

def _lfn_entries(name, short):
    csum = 0
    for c in short:
        csum = (((csum & 1) << 7) + (csum >> 1) + c) & 0xFF
    u = [ord(c) for c in name] + [0]
    u += [0xFFFF] * (-len(u) % 13)
    parts = [u[i:i + 13] for i in range(0, len(u), 13)]
    out = []
    for seq in range(len(parts), 0, -1):
        p = parts[seq - 1]
        w = lambda a: b"".join(struct.pack("<H", x) for x in a)
        out.append(bytes([seq | (0x40 if seq == len(parts) else 0)]) + w(p[0:5]) + b"\x0f\x00" + bytes([csum])
                   + w(p[5:11]) + b"\x00\x00" + w(p[11:13]))
    return out

def pack_fat(files):
    """{name: bytes} (root directory only) → a FAT16 image. Same files → same bytes."""
    names = sorted(files)
    need = sum(max(1, -(-len(files[n]) // SEC)) for n in names)
    clusters = max(MIN_CLUSTERS, need + 16)
    fat_secs = -(-(clusters + 2) * 2 // SEC)
    root_secs = ROOT_ENTRIES * 32 // SEC
    data0 = 1 + 2 * fat_secs + root_secs
    total = data0 + clusters
    vol = hashlib.sha256(b"".join(n.encode() + files[n] for n in names)).digest()[:4]
    bs = bytearray(SEC)
    bs[0:3] = b"\xeb\x3c\x90"; bs[3:11] = b"POKE    "
    struct.pack_into("<HBHBHHBHHHII", bs, 11, SEC, 1, 1, 2, ROOT_ENTRIES, total if total < 65536 else 0, 0xF8,
                     fat_secs, 32, 64, 0, total if total >= 65536 else 0)
    struct.pack_into("<BBB4s11s8s", bs, 36, 0x80, 0, 0x29, vol, b"POKE-BOOT  ", b"FAT16   ")
    bs[510:512] = b"\x55\xaa"
    fat = [0xFFF8, 0xFFFF] + [0] * clusters
    root, data, used, nxt = bytearray(), bytearray(), set(), 2
    for n in names:
        short, long_ = _short(n, used); used.add(short)
        if long_:
            for e in _lfn_entries(n, short): root += e
        body = files[n]
        k = -(-len(body) // SEC)
        first = nxt if k else 0
        for i in range(k):
            fat[nxt + i] = nxt + i + 1 if i < k - 1 else 0xFFFF
        nxt += k
        data += body + b"\0" * (-len(body) % SEC)
        root += short + bytes([0x20, 0, 0]) + struct.pack("<HHHHHHHI", FIXED_TIME, FIXED_DATE, FIXED_DATE, 0,
                                                            FIXED_TIME, FIXED_DATE, first, len(body))
    if len(root) > ROOT_ENTRIES * 32:
        raise ValueError("too many files for the root directory")
    fatb = b"".join(struct.pack("<H", x) for x in fat)
    fatb += b"\0" * (fat_secs * SEC - len(fatb))
    img = bytes(bs) + fatb + fatb + bytes(root) + b"\0" * (root_secs * SEC - len(root)) + bytes(data)
    return img + b"\0" * (total * SEC - len(img))

def unpack_fat(img):
    """A FAT16 image (root directory) → {name: bytes}. Long names when present, else 8.3 in lower case."""
    sec, spc, res, nfat, nroot = struct.unpack_from("<HBHBH", img, 11)
    fat_secs = struct.unpack_from("<H", img, 22)[0]
    if sec not in (512, 1024, 2048, 4096) or not spc or not nfat:
        raise ValueError("not a FAT image")
    fat0 = res * sec
    root0 = fat0 + nfat * fat_secs * sec
    data0 = root0 + nroot * 32
    cl = spc * sec
    out, lfn = {}, {}
    for i in range(nroot):
        e = img[root0 + 32 * i: root0 + 32 * i + 32]
        if e[0] == 0: break
        if e[0] == 0xE5: lfn = {}; continue
        if e[11] == 0x0F:
            seq = e[0] & 0x1F
            chars = struct.unpack("<5H", e[1:11]) + struct.unpack("<6H", e[14:26]) + struct.unpack("<2H", e[28:32])
            lfn[seq] = "".join(chr(c) for c in chars if c not in (0, 0xFFFF)); continue
        if e[11] & 0x18:                                 # volume label or directory
            lfn = {}; continue
        name = "".join(lfn[k] for k in sorted(lfn)) if lfn else (
            e[0:8].decode().strip() + ("." + e[8:11].decode().strip() if e[8:11].strip() else "")).lower()
        lfn = {}
        c, size = struct.unpack_from("<H", e, 26)[0], struct.unpack_from("<I", e, 28)[0]
        body, seen = bytearray(), set()
        while 2 <= c < 0xFFF8 and len(body) < size:
            if c in seen: raise ValueError("FAT loop")
            seen.add(c)
            body += img[data0 + (c - 2) * cl: data0 + (c - 1) * cl]
            c = struct.unpack_from("<H", img, fat0 + 2 * c)[0]
        out[name] = bytes(body[:size])
    return out

# ── the hub: an image → boot.img + boot.sig ─────────────────────────────
def pack(kernel, root=ROOT, manifest=None, firmware=True):
    """Our image (bytes, final: RCFG already patched) → boot.img bytes. Holds the firmware's config.txt and,
    when present in netboot/root, the firmware and device tree the Pi needs (the twin needs only the kernel)."""
    files = {KERNEL: kernel}
    cfg = os.path.join(root, "config.txt")
    files["config.txt"] = open(cfg, "rb").read() if os.path.exists(cfg) else b"arm_64bit=1\nkernel=sentence.img\n"
    if firmware:                                     # the pair config.txt selects: gpu_mem=16 → the cut-down one
        cd = any(l.split("#", 1)[0].strip() == "gpu_mem=16" for l in files["config.txt"].decode(errors="replace").splitlines())
        for f in (FIRMWARE[0:2] if cd else FIRMWARE[2:4]) + FIRMWARE[4:]:
            p = os.path.join(root, f)
            if os.path.exists(p): files[f] = open(p, "rb").read()
    files["poke.json"] = json.dumps({"kernel_sha256": hashlib.sha256(kernel).hexdigest(), **(manifest or {})},
                                    sort_keys=True, ensure_ascii=False).encode()
    return pack_fat(files)

def seal(kernel, root=ROOT, manifest=None, key=None, firmware=True, wait_ms=3000, target="twin"):
    """→ (boot.img bytes, boot.sig text). Only call this for an image that passed the hub's check.
    The kernel is stamped with the ticket key first (stamp), so the signature covers the key too."""
    img = pack(stamp(kernel, pub_of(key), wait_ms, target), root, manifest, firmware)
    return img, boot_sig(img, key)

# ── freshness: one ticket per boot the hub means (edge/sentence/rt_fresh.h) ──
# The bootloader checks who signed boot.img, not when. The runtime asks the hub for a ticket on a nonce
# from the SoC's RNG; the hub signs it only for the image it means to run now (netboot/tftpd.py desk).
TKEY_MAGIC, TKEY_SIZE = 0x59454B54, 32 + 2 * 256          # "TKEY", rt_fresh.c

def pub_of(key=None):
    """The public key for a private key path (None: the hub's own)."""
    if key is None:
        return ensure_key()[1]
    return subprocess.run(["openssl", "pkey", "-in", key, "-pubout"], check=True, capture_output=True).stdout

def stamp(kernel, pub=None, wait_ms=3000, target="twin"):
    """Write the ticket key into the image's TKEY block: e, n, R² mod n and -n⁻¹ mod 2³² (what the node's
    Montgomery arithmetic needs), the image length the node hashes, how long it waits, and where the nonce
    comes from (target "pi": the SoC's RNG200; the twin has none: timing jitter). Same input → same bytes."""
    n, e = public_key(pub or ensure_key()[1])
    if n.bit_length() != 2048 or e != 65537:
        raise ValueError("the ticket key must be RSA-2048 with e = 65537")
    at = [i for i in range(0, len(kernel) - TKEY_SIZE + 1, 16)
          if struct.unpack_from("<II", kernel, i) == (TKEY_MAGIC, 1)]
    if len(at) != 1:
        raise ValueError(f"TKEY block not found exactly once in the image ({len(at)}): its runtime predates fresh boots")
    i, data = at[0], bytearray(kernel)
    struct.pack_into("<IIIIIII", data, i, TKEY_MAGIC, 1, len(kernel), e, (-pow(n, -1, 1 << 32)) % (1 << 32), wait_ms,
                     0 if target == "pi" else 1)
    data[i + 32:i + 288] = n.to_bytes(256, "little")
    data[i + 288:i + 544] = (pow(2, 4096, n)).to_bytes(256, "little")
    return bytes(data)

def ticket_message(nonce, image_sha256):
    return b"POKE-TICKET1" + nonce + image_sha256

def ticket(nonce, image_sha256, key=None):
    """The hub's answer to POKE-NONCE: the line the node reads."""
    return b"POKE-TICKET " + sign(ticket_message(nonce, image_sha256), key).hex().encode() + b"\n"

def open_boot(boot_img, sig_text, pub):
    """The node's side: verify, then open the ramdisk. → (files, None) or (None, why)."""
    ok, why = verify(boot_img, sig_text, pub)
    if not ok:
        return None, why
    try:
        return unpack_fat(boot_img), None
    except (ValueError, struct.error, UnicodeDecodeError) as e:
        return None, f"boot.img is not a FAT ramdisk ({e})"

if __name__ == "__main__":
    a = sys.argv[1:] or ["key"]
    if a[0] == "key":
        k, p = ensure_key()
        print(f"private {k} (hub only)\npublic  {p}\nfingerprint {fingerprint(p)}")
    elif a[0] == "pack":
        data = open(a[1], "rb").read()
        if "--target" in a:
            import rtcfg
            data = rtcfg.for_target(data, a[a.index("--target") + 1])
        d = a[a.index("-o") + 1] if "-o" in a else "."
        tgt = a[a.index("--target") + 1] if "--target" in a else "twin"
        img, sig = seal(data, manifest={"source": os.path.basename(a[1]), "target": tgt}, target=tgt)
        open(os.path.join(d, "boot.img"), "wb").write(img); open(os.path.join(d, "boot.sig"), "w").write(sig)
        print(f"{d}/boot.img {len(img)} B, {d}/boot.sig")
    elif a[0] == "verify":
        ok, why = verify(open(a[1], "rb").read(), open(a[2]).read(), a[3] if len(a) > 3 else ensure_key()[1])
        print("ok" if ok else f"REFUSED: {why}"); sys.exit(0 if ok else 1)
    elif a[0] == "list":
        for n, b in unpack_fat(open(a[1], "rb").read()).items():
            print(f"{len(b):>9}  {n}")
