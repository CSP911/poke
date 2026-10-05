"""POKE edge store: a record log on a raw SD partition, through the sdcard resident.

No filesystem. The partition is a sequence of 512-byte blocks:

  block 0        superblock   "PSTR" ver u32, total u32, next u32, nrec u32, seq u32, (zeros), crc32 @508
  block 1..      records      header "PREC" seq u32, size u32, ts u32, crc u32, kind[16], name[64]
                              followed by ceil(size/512) data blocks, then the next record

Records are appended; the newest record with a given name is the current
one. Enough for a skill library and the hub's history, and simple enough to
be read back by a few dozen lines of C on the edge one day.

    st = EdgeStore(link)              # link = pokeedge.connect(host)
    st.format(start_lba, nblocks)     # once (destroys what was there)
    st.put("fist", b"...", kind="skill")
    st.list(); st.get("fist")
"""
import struct, time, zlib
import pokeedge

BLK = 512
MAGIC_SB, MAGIC_REC = b"PSTR", b"PREC"
HDR = struct.Struct("<4sIIII16s64s")          # 100 bytes

class StoreError(RuntimeError):
    pass

class EdgeStore:
    def __init__(self, link, start=None):
        self.link = link
        self.start = start if start is not None else self._find_partition()
        self.total = self.next = self.nrec = self.seq = 0
        self._cache = None

    # ── block I/O through the resident ──
    def _rd(self, lba, n=1):
        out = b""
        while n:
            k = min(n, 2)
            st, d = pokeedge.sreq(self.link, 2, struct.pack("<II", self.start + lba, k))
            if st:
                raise StoreError(f"read lba {self.start + lba} failed ({st})")
            out += d; lba += k; n -= k
        return out
    def _wr(self, lba, data):
        assert len(data) % BLK == 0
        for i in range(0, len(data), BLK):
            st, _ = pokeedge.sreq(self.link, 3, struct.pack("<I", self.start + lba + i // BLK) + data[i:i + BLK])
            if st:
                raise StoreError(f"write lba {self.start + lba + i // BLK} failed ({st})")

    def _find_partition(self):
        """The store partition is the MBR entry of type 0xDA (or, before format, the one the user chose)."""
        st, mbr = pokeedge.sreq(self.link, 2, struct.pack("<II", 0, 1))
        for p in range(4):
            e = mbr[446 + p * 16: 446 + p * 16 + 16]
            if e[4] == 0xDA:
                return struct.unpack("<I", e[8:12])[0]
        raise StoreError("no POKE store partition (type 0xDA) in the MBR")

    @staticmethod
    def mark_partition(link, index, ptype=0xDA):
        """Change one MBR entry's type byte (read, patch one byte, write, verify)."""
        st, mbr = pokeedge.sreq(link, 2, struct.pack("<II", 0, 1))
        if mbr[510:512] != b"\x55\xAA":
            raise StoreError("no MBR signature")
        e = 446 + index * 16
        start, n = struct.unpack("<II", mbr[e + 8:e + 16])
        new = bytearray(mbr); new[e + 4] = ptype
        st, _ = pokeedge.sreq(link, 3, struct.pack("<I", 0) + bytes(new))
        st2, back = pokeedge.sreq(link, 2, struct.pack("<II", 0, 1))
        if st or st2 or back != bytes(new):
            raise StoreError("MBR write/verify failed")
        return start, n

    # ── superblock ──
    def _sb_bytes(self):
        b = bytearray(BLK)
        b[:20] = struct.pack("<4sIIII", MAGIC_SB, 1, self.total, self.next, self.nrec)
        b[20:24] = struct.pack("<I", self.seq)
        b[508:512] = struct.pack("<I", zlib.crc32(bytes(b[:508])))
        return bytes(b)
    def _sb_save(self):
        self._wr(0, self._sb_bytes())
    def open(self):
        b = self._rd(0)
        if b[:4] != MAGIC_SB or struct.unpack("<I", b[508:512])[0] != zlib.crc32(b[:508]):
            raise StoreError("no store here (format it first)")
        _, ver, self.total, self.next, self.nrec = struct.unpack("<4sIIII", b[:20])
        self.seq = struct.unpack("<I", b[20:24])[0]
        return self
    def format(self, nblocks, wipe_ext=True):
        """Create an empty store. wipe_ext also clears the ext2/3/4 superblock that used to live here."""
        self.total, self.next, self.nrec, self.seq = nblocks, 1, 0, 0
        self._sb_save()
        if wipe_ext:
            self._wr(2, bytes(BLK) * 2)
        self._cache = None
        return self

    # ── records ──
    def put(self, name, data, kind="blob"):
        if len(name.encode()) > 63 or len(kind.encode()) > 15:
            raise StoreError("name/kind too long")
        nblk = (len(data) + BLK - 1) // BLK
        if self.next + 1 + nblk > self.total:
            raise StoreError("store full")
        self.seq += 1
        hdr = bytearray(BLK)
        hdr[:HDR.size] = HDR.pack(MAGIC_REC, self.seq, len(data), int(time.time()), zlib.crc32(data),
                                  kind.encode().ljust(16, b"\0"), name.encode().ljust(64, b"\0"))
        body = data + bytes(nblk * BLK - len(data))
        self._wr(self.next, bytes(hdr) + body)
        self.next += 1 + nblk; self.nrec += 1
        self._sb_save()
        if self._cache is not None:
            self._cache.append({"seq": self.seq, "name": name, "kind": kind, "size": len(data), "ts": int(time.time()), "lba": self.next - 1 - nblk})
        return self.seq
    def scan(self):
        """Walk the log once; cached until the next put."""
        if self._cache is not None:
            return self._cache
        recs, lba = [], 1
        while lba < self.next:
            h = self._rd(lba)
            if h[:4] != MAGIC_REC:
                break
            _, seq, size, ts, crc, kind, name = HDR.unpack(h[:HDR.size])
            recs.append({"seq": seq, "name": name.rstrip(b"\0").decode(), "kind": kind.rstrip(b"\0").decode(),
                         "size": size, "ts": ts, "crc": crc, "lba": lba})
            lba += 1 + (size + BLK - 1) // BLK
        self._cache = recs
        return recs
    def list(self, kind=None):
        latest = {}
        for r in self.scan():
            if kind is None or r["kind"] == kind:
                latest[r["name"]] = r                     # newest wins
        return sorted(latest.values(), key=lambda r: r["seq"])
    def get(self, name):
        rs = [r for r in self.scan() if r["name"] == name]
        if not rs:
            raise StoreError(f"no record '{name}'")
        r = rs[-1]
        d = self._rd(r["lba"] + 1, (r["size"] + BLK - 1) // BLK)[:r["size"]]
        if zlib.crc32(d) != r["crc"]:
            raise StoreError(f"record '{name}' is corrupt")
        return d

def main():
    import os, sys, json
    host = os.environ.get("POKE_HOST", "10.0.0.2")
    link = pokeedge.connect(host)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "format":                       # format <mbr-partition-index>
        idx = int(sys.argv[2])
        start, n = EdgeStore.mark_partition(link, idx)
        st = EdgeStore(link, start).format(n)
        print(f"formatted: partition {idx} → POKE store, {n} blocks ({n / 2048 / 1024:.1f} GB) at lba {start}")
        return
    st = EdgeStore(link).open()
    if cmd == "list":
        print(f"store: {st.nrec} records, {st.next - 1} / {st.total} blocks used")
        for r in st.list():
            print(f"  {r['seq']:5d}  {r['kind']:<8} {r['name']:<32} {r['size']:7d} B  {time.strftime('%Y-%m-%d %H:%M', time.localtime(r['ts']))}")
    elif cmd == "put":                        # put <name> <file> [kind]
        seq = st.put(sys.argv[2], open(sys.argv[3], "rb").read(), sys.argv[4] if len(sys.argv) > 4 else "blob")
        print(f"stored '{sys.argv[2]}' as record {seq}")
    elif cmd == "get":                        # get <name> [outfile]
        d = st.get(sys.argv[2])
        if len(sys.argv) > 3:
            open(sys.argv[3], "wb").write(d); print(f"{len(d)} B → {sys.argv[3]}")
        else:
            sys.stdout.write(d.decode(errors="replace"))

if __name__ == "__main__":
    main()
