"""Run a generated skill on POKE, not on the Mac.

The skill (C) is built for aarch64 with the skill runtime (crt + math) and
linked as an EL0 unit; the kernel runs it in its own address space, steps it
once per control tick, and returns only targets that passed the kernel's
safety filter. The body itself (simulated here, servos later) stays on the
hub side of the wire.

    edge = PokeSkill("10.0.0.2")
    edge.load(build(src), limits, axes)
    edge.attempt(params)
    cmd, safety = edge.step(t, tick, q, imu)
"""
import os, socket, struct, subprocess, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
KERNEL = os.path.join(ROOT, "edge", "kernel", "pi4")
RT = os.path.join(ROOT, "edge", "library", "pi4", "skillrt")
UNIT_LD = os.path.join(ROOT, "edge", "library", "pi4", "unit.ld")
BUILD = os.path.join(HERE, "build")

def build(src, name=None):
    """C skill → flat aarch64 EL0 unit image (crt first, then skill, then math)."""
    os.makedirs(BUILD, exist_ok=True)
    name = name or os.path.splitext(os.path.basename(src))[0]
    elf, out = os.path.join(BUILD, f"{name}.elf"), os.path.join(BUILD, f"{name}.unit.bin")
    flags = ["-ffreestanding", "-nostdlib", "-O2", "-mcpu=cortex-a72", "-ffunction-sections",
             "-fno-stack-protector", "-I", KERNEL, "-isystem", RT]
    objs = []
    for s in (os.path.join(RT, "skill_crt.c"), src, os.path.join(RT, "umath.c")):
        o = os.path.join(BUILD, f"{name}-{os.path.splitext(os.path.basename(s))[0]}.o")
        r = subprocess.run(["aarch64-elf-gcc", *flags, "-c", "-o", o, s], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stderr.strip())
        objs.append(o)
    r = subprocess.run(["aarch64-elf-ld", "-T", UNIT_LD, "-nostdlib", "--gc-sections", "-e", "_start",
                        "-o", elf, *objs], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    subprocess.run(["aarch64-elf-objcopy", "-O", "binary", elf, out], check=True)
    return open(out, "rb").read()

def fletcher32(b):
    s1 = s2 = 0
    for x in b:
        s1 = (s1 + x) % 65535; s2 = (s2 + s1) % 65535
    return (s2 << 16) | s1

class EdgeError(RuntimeError):
    pass

def frame(payload):
    return b"POKE" + struct.pack("<I", len(payload)) + payload

class UdpLink:
    """A real POKE edge on the wire (Pi 4 over GENET)."""
    def __init__(self, host, port=5555):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    def request(self, payload, timeout=0.5):
        self.sock.settimeout(timeout)
        for _ in range(3):
            self.sock.sendto(frame(payload), self.addr)
            try:
                data, _ = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            n = struct.unpack("<I", data[4:8])[0]
            return data[8:8 + n]
        raise EdgeError(f"no response from {self.addr[0]}")

class TcpLink:
    """The QEMU twin: same frames over the twin's protocol UART, exposed as TCP."""
    def __init__(self, host, port):
        for i in range(50):                            # the twin opens its socket a moment after boot
            try:
                self.sock = socket.create_connection((host, port), timeout=5); break
            except ConnectionRefusedError:
                if i == 49:
                    raise EdgeError(f"nothing listening at {host}:{port}")
                time.sleep(0.1)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buf = b""
    def _read(self, n, deadline):
        while len(self.buf) < n:
            self.sock.settimeout(max(0.01, deadline - time.time()))
            try:
                d = self.sock.recv(4096)
            except socket.timeout:
                raise EdgeError("no response from twin")
            if not d:
                raise EdgeError("twin closed the connection")
            self.buf += d
        out, self.buf = self.buf[:n], self.buf[n:]
        return out
    def request(self, payload, timeout=0.5):
        self.sock.sendall(frame(payload))
        deadline = time.time() + max(timeout, 0.5)
        while True:                                   # resync on the RESP magic
            h = self._read(8, deadline)
            if h[:4] == b"RESP":
                break
            self.buf = h[1:] + self.buf
        n = struct.unpack("<I", h[4:8])[0]
        return self._read(n, deadline)

def connect(host="10.0.0.2"):
    """'10.0.0.2' → UDP to a real edge; 'tcp://127.0.0.1:18081' → the twin."""
    if host.startswith("tcp://"):
        h, p = host[6:].rsplit(":", 1)
        return TcpLink(h, int(p))
    return UdpLink(host)

class Twin:
    """Boot the POKE twin kernel in QEMU (raspi4b) with its protocol UART on TCP.

        with pokeedge.Twin() as host:        # host = 'tcp://127.0.0.1:<port>'
            edge = pokeedge.PokeSkill(host)
    """
    def __init__(self, port=None, console=None, sd=None, extra=(), monitor=False):
        self.port = port or 18081 + int.from_bytes(os.urandom(2), "little") % 1000
        self.console = console or os.path.join(BUILD, "twin-console.log")
        self.sd = sd                                   # raw image for the emulated SD slot
        self.extra = list(extra)                       # more QEMU args: virtual devices to plug in
        self.mon_port = self.port + 1000 if monitor else None
        self.proc = None
    def __enter__(self):
        os.makedirs(BUILD, exist_ok=True)
        open(self.console, "w").close()                # a fresh log: never trust an old prompt
        img = os.path.join(KERNEL, "kernel8-qemu.img")
        subprocess.run(["make", "-C", KERNEL, "kernel8-qemu.img"], capture_output=True, check=True)
        self.proc = subprocess.Popen(
            ["qemu-system-aarch64", "-M", "raspi4b", "-kernel", img, "-display", "none", "-monitor", "none",
             "-chardev", f"file,id=con,path={self.console}", "-serial", "chardev:con",
             "-chardev", f"socket,id=net,host=127.0.0.1,port={self.port},server=on,wait=off", "-serial", "chardev:net"]
            + (["-drive", f"file={self.sd},if=sd,format=raw"] if self.sd else [])
            + (["-monitor", f"tcp:127.0.0.1:{self.mon_port},server=on,wait=off"] if self.mon_port else [])
            + self.extra,
            stdout=open(os.path.join(BUILD, "twin-qemu.log"), "w"), stderr=subprocess.STDOUT)
        t0 = time.time()
        while time.time() - t0 < 20:                  # wait for the prompt on the console
            try:
                if "poke-pi4>" in open(self.console).read():
                    break
            except FileNotFoundError:
                pass
            if self.proc.poll() is not None:
                raise EdgeError("qemu exited while booting the twin")
            time.sleep(0.2)
        else:
            self.__exit__(None, None, None); raise EdgeError("twin did not boot (see build/twin-console.log)")
        return f"tcp://127.0.0.1:{self.port}"
    def monitor(self, cmd):
        """Run a QEMU monitor command (needs monitor=True), e.g. 'qom-set t1 temperature 23500'."""
        with socket.create_connection(("127.0.0.1", self.mon_port), timeout=5) as m:
            m.settimeout(1.0); buf = b""
            try:
                while b"(qemu)" not in buf: buf += m.recv(4096)
            except socket.timeout: pass
            m.sendall((cmd + "\n").encode()); out = b""
            try:
                while True:
                    d = m.recv(4096)
                    if not d: break
                    out += d
                    if out.rstrip().endswith(b"(qemu)"): break
            except socket.timeout: pass
            return out.decode(errors="replace")
    def __exit__(self, *a):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                self.proc.kill()

def upload(link, image):
    for off in range(0, len(image), 1024):
        r = link.request(b"EXLD" + struct.pack("<I", off) + image[off:off + 1024], timeout=2)
        if not r.startswith(b"ok"):
            raise EdgeError(f"upload failed at {off}: {r!r}")

def run_unit(link, image, mappings=(), timeout=30):
    """Run a flat EL0 unit image with capability mappings [(pa, len, 'device'|'nc'), ...].
    Returns the kernel's reply dict: code, pages, log."""
    import json
    upload(link, image)
    extra = struct.pack("<I", len(mappings))
    for pa, ln, attr in mappings:
        extra += struct.pack("<QQI", pa, ln, 1 if attr == "nc" else 0)
    r = link.request(b"URUN" + struct.pack("<II", len(image), fletcher32(image)) + extra, timeout=timeout)
    return json.loads(r.decode(errors="replace"), strict=False)

def build_unit(src, name=None, defines=()):
    """Plain C → flat EL0 unit image (no runtime, the unit's own _start)."""
    os.makedirs(BUILD, exist_ok=True)
    name = name or os.path.splitext(os.path.basename(src))[0]
    o, elf, out = (os.path.join(BUILD, f"{name}.{e}") for e in ("o", "elf", "unit.bin"))
    r = subprocess.run(["aarch64-elf-gcc", "-ffreestanding", "-nostdlib", "-fno-builtin", "-fno-stack-protector", "-O1",
                        "-mstrict-align", "-mcpu=cortex-a72", "-ffunction-sections", *[f"-D{d}" for d in defines],
                        "-c", "-o", o, src], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    r = subprocess.run(["aarch64-elf-ld", "-T", UNIT_LD, "-nostdlib", "-o", elf, o], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    subprocess.run(["aarch64-elf-objcopy", "-O", "binary", elf, out], check=True)
    return open(out, "rb").read()

RESIDENT_LD = os.path.join(ROOT, "edge", "library", "pi4", "resident.ld")

def build_resident(src, name=None, defines=(), include=None):
    """Resident C source → flat image for RSLD (resident.ld, kernel prepends its crt)."""
    os.makedirs(BUILD, exist_ok=True)
    name = name or os.path.splitext(os.path.basename(src))[0]
    o, elf, out = (os.path.join(BUILD, f"{name}.{e}") for e in ("o", "elf", "res.bin"))
    r = subprocess.run(["aarch64-elf-gcc", "-ffreestanding", "-nostdlib", "-fno-builtin", "-fno-stack-protector", "-Os",
                        "-mstrict-align", "-mcpu=cortex-a72", "-ffunction-sections", *[f"-D{d}" for d in defines],
                        *(["-I", include] if include else []), "-c", "-o", o, src], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    r = subprocess.run(["aarch64-elf-ld", "-T", RESIDENT_LD, "-nostdlib", "-o", elf, o], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    subprocess.run(["aarch64-elf-objcopy", "-O", "binary", elf, out], check=True)
    return open(out, "rb").read()

def run_resident(link, image, mappings, name):
    """Inject a resident driver (RSLD) with its capability mappings. Returns the kernel's reply dict."""
    import json
    upload(link, image)
    extra = struct.pack("<I", len(mappings))
    for pa, ln, attr in mappings:
        extra += struct.pack("<QQI", pa, ln, 1 if attr == "nc" else 0)
    extra += name.encode() + b"\0"
    r = link.request(b"RSLD" + struct.pack("<II", len(image), fletcher32(image)) + extra, timeout=20)
    return json.loads(r.decode(errors="replace"), strict=False)

def sreq(link, op, data=b"", timeout=6):
    """Service request to the resident: returns (status, response bytes)."""
    r = link.request(b"SREQ" + struct.pack("<I", op) + data, timeout=timeout)
    if not r.startswith(b"SRSP"):
        raise EdgeError(r.decode(errors="replace"))
    return struct.unpack("<I", r[4:8])[0], r[8:]

def patch_params(image, values, magic_base=0x7a57a001):
    """Replace the probe's 0x7a57a00N7a57a00N parameter magics with values (parameter patching)."""
    img = bytearray(image)
    for i, v in enumerate(values):
        m = struct.pack("<Q", ((magic_base + i) << 32) | (magic_base + i))
        at = img.find(m)
        if at < 0:
            raise EdgeError(f"param magic {i} not found in image")
        img[at:at + 8] = struct.pack("<Q", v)
    return bytes(img)

class PokeSkill:
    def __init__(self, host="10.0.0.2"):
        """host: an address, or an existing link (the twin accepts only one connection)."""
        self.link = host if isinstance(host, (UdpLink, TcpLink)) else connect(host)
        self.n = 0

    def request(self, payload, timeout=None):
        return self.link.request(payload, timeout or 0.5)

    def load(self, image, limits, axes):
        for off in range(0, len(image), 1024):
            r = self.request(b"EXLD" + struct.pack("<I", off) + image[off:off + 1024], timeout=2)
            if not r.startswith(b"ok"):
                raise EdgeError(f"upload failed at {off}: {r!r}")
        n = len(limits); self.n = n
        body = struct.pack("<I", n) + struct.pack(f"<{n}f", *limits) + struct.pack(f"<{n}I", *axes)
        r = self.request(b"MLOD" + struct.pack("<II", len(image), fletcher32(image)) + body, timeout=5)
        if b"error" in r:
            raise EdgeError(r.decode(errors="replace"))
        return r.decode()

    def attempt(self, params):
        p = (list(params) + [0.0] * 8)[:8]
        r = self.request(b"MATT" + struct.pack("<8f", *p))
        if b"error" in r:
            raise EdgeError(r.decode(errors="replace"))

    def step(self, t, tick, q, imu=(0, 0, 0, 0, 0, 0)):
        n = self.n
        r = self.request(b"MSTP" + struct.pack(f"<fI{n}f6f", t, tick, *q, *imu))
        if not r.startswith(b"MOUT"):
            raise EdgeError(r.decode(errors="replace"))
        vals = struct.unpack(f"<{n}f5I", r[4:4 + n * 4 + 20])
        cmd, (clamped, rate, nonfinite, estop, step_us) = list(vals[:n]), vals[n:]
        return cmd, {"clamped": clamped, "rate_limited": rate, "nonfinite": nonfinite,
                     "estop": bool(estop), "step_us": step_us}

    def stop(self):
        try:
            self.request(b"MSTO")
        except EdgeError:
            pass

if __name__ == "__main__":
    img = build(os.path.join(HERE, "skills", "wave.c"))
    print(f"wave.c → EL0 unit image {len(img)} bytes")
