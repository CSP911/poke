#!/usr/bin/env python3
"""불변식 · 격리: 허브가 서명한 이미지만 노드에서 켜진다 — 넷부팅 경로(허브 tftpd → 가상 부트로더 → 트윈).

노드에는 우리 것이 아무것도 남지 않으니, 노드가 무엇이 될지는 매번 네트워크에서 온다. TFTP에는 인증이 없다.
그래서 Pi 4 부트로더의 서명 부팅 형식(boot.img + boot.sig, RSA-2048, seal.py)을 쓰고, 가상 부트로더
(netboot/vboot.py --signed)가 실기기 부트로더처럼 확인한다. 허브의 키는 이 시험만의 임시 키다(POKE_KEYS).

  S1 허브가 서명한 이미지는 켜지고 결과가 옳다, 보고에 서명 키와 이미지 지문이 남는다
  S2 boot.sig가 없으면 켜지지 않는다
  S3 boot.img의 한 바이트를 바꾸면 켜지지 않는다
  S4 다른 키로 서명한 이미지(게이트를 움직이는 이미지)는 켜지지 않는다
  S5 바꾼 boot.img에 맞춰 해시 줄만 고쳐도 켜지지 않는다
  S6 서명 부팅 노드는 서명 없는 일반 이미지(sentence.img)를 묻지도 않는다
  S7 같은 이미지 → 같은 boot.img 바이트 (같은 역할 = 같은 발음, R4와 같은 약속)
  S8 boot.sig는 openssl(= rpi-eeprom-digest가 쓰는 도구)로도 검증된다

재전송: Pi 부트로더는 "누가" 서명했는지만 본다. 그래서 이미지 안의 런타임이 op 전에 허브에게 묻는다 —
노드가 뽑은 일회용 값(nonce)에 허브가 서명한 티켓이 있어야 op가 돈다(edge/sentence/rt_fresh.h).
  S9  방금 쓴 이미지를 그대로 다시 보내면 부트로더는 받지만 허브가 티켓을 주지 않아 op가 돌지 않는다
  S10 허브가 예전에 서명한(지금 의도하지 않은) 게이트 이미지를 보내도 게이트는 움직이지 않는다
  S11 노드는 스스로 확인한다: 녹음된 옛 티켓, 다른 키의 티켓, 깨진 티켓, 무응답 — 모두 op 없이 꺼진다
  S12 부팅마다 nonce가 다르다, 노드가 보낸 이미지 지문 = 허브가 봉인한 커널의 지문
"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "test"))
import isolate  # noqa: E402,F401 — 소유자 기록 보호
import os, sys, json, time, shutil, tempfile, hashlib, subprocess
HERE = os.path.dirname(os.path.abspath(__file__)); SENT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, SENT)
TMP = tempfile.mkdtemp(prefix="poke-sign-")
os.environ["POKE_KEYS"] = os.path.join(TMP, "hub-keys")           # never the hub's real key
import say, seal
sys.path.insert(0, os.path.join(SENT, "netboot")); import tftpd as hubd     # the hub's own intent record (what --pend writes)

ok = n = 0
def check(name, cond, detail=""):
    global ok, n
    n += 1; ok += bool(cond)
    print(f"{'PASS' if cond else 'FAIL'} {name} {detail}", flush=True)

NB = os.path.join(SENT, "netboot")
ROOT, OUT = os.path.join(TMP, "root"), os.path.join(TMP, "out")
os.makedirs(ROOT); os.makedirs(OUT)
for f in ("config.txt", *seal.FIRMWARE):                          # the firmware blobs are git-ignored: copied when present
    if os.path.exists(os.path.join(NB, "root", f)):
        shutil.copy(os.path.join(NB, "root", f), ROOT)
FIRMWARE = all(os.path.exists(os.path.join(ROOT, f)) for f in ("start4cd.elf", "fixup4cd.dat"))
PUB = seal.ensure_key()[1]
ATTACKER = os.path.join(TMP, "attacker.pem")
subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", ATTACKER], check=True, capture_output=True)

H = '#include "rt.h"\n'
READ = H + '#include "tmp105.h"\nvoid op_main(void){ long t; int e = tmp105_read_mC(0x48, &t); if (e) { fail("tmp105"); return; } report_int("mC", t); }'
WRITE = H + '#include "led.h"\nvoid op_main(void){ int e = led_set(0x60, 3, 0); if (e) { fail("led"); return; } report_int("led3", 1); }'
_, _, read_img = say.build(READ, "tt-sign-read")
_, _, write_img = say.build(WRITE, "tt-sign-gate")

port = 17500 + os.getpid() % 1000
log = open(os.path.join(TMP, "procs.log"), "w")
env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
tftpd = subprocess.Popen([sys.executable, "-u", os.path.join(NB, "tftpd.py"), "--port", str(port), "--root", ROOT], stdout=log, stderr=subprocess.STDOUT, env=env)
vboot = subprocess.Popen([sys.executable, "-u", os.path.join(NB, "vboot.py"), "--port", str(port), "--out", OUT, "--signed", PUB,
                          "--file-timeout", "5", "--restart-s", "0.1"], stdout=log, stderr=subprocess.STDOUT, env=env, cwd=NB)
P = os.path.join(ROOT, "pending.img")

def offer(kernel, bootimg=None, bootsig=None):
    """Put files on the hub's TFTP root the way an attacker on the wire (or the hub) could: pending.img last."""
    before = set(os.listdir(OUT))
    for suf, body in ((".bootimg", bootimg), (".bootsig", bootsig)):
        if body is None:
            if os.path.exists(P + suf): os.remove(P + suf)
        else:
            open(P + suf, "wb").write(body if isinstance(body, bytes) else body.encode())
    open(P + ".tmp", "wb").write(kernel); os.replace(P + ".tmp", P)
    return before

def report(before, wait_s=40):
    t0 = time.time()
    while time.time() - t0 < wait_s:
        new = sorted((f for f in set(os.listdir(OUT)) - before if f.endswith(".json")), key=lambda f: int(f[:-5]))
        if new:
            time.sleep(0.05); return json.load(open(os.path.join(OUT, new[-1])))
        time.sleep(0.05)
    return None

def hub_pend(img):
    before = set(os.listdir(OUT))
    subprocess.run([sys.executable, os.path.join(NB, "tftpd.py"), "--pend", img, "--root", ROOT], check=True, capture_output=True, env=env)
    return before

def refused(b, why):
    return bool(b) and b.get("refused", "").startswith(why) and b.get("powered_off") is False and "boot_to_off_ms" not in b

try:
    time.sleep(1.0)
    kernel = open(read_img, "rb").read()

    # S1 — the hub's sealed image boots
    if FIRMWARE:
        b = report(hub_pend(read_img))
        good_img, good_sig = open(P + ".bootimg.booted", "rb").read(), open(P + ".bootsig.booted").read()
        mC = ((b or {}).get("result") or {}).get("mC")
        check("S1-signed-image-boots", bool(b) and b.get("signed") and b.get("powered_off") and mC is not None and abs(mC - 23750) <= 500,
              f"mC={mC} off={(b or {}).get('powered_off')} key={(b or {}).get('key')}")
        sealed = seal.unpack_fat(good_img)[seal.KERNEL]
        check("S1-report-names-key-and-image", (b or {}).get("key") == seal.fingerprint(PUB)
              and ((b or {}).get("manifest") or {}).get("kernel_sha256") == hashlib.sha256(sealed).hexdigest(), str((b or {}).get("manifest")))
        fr = (b or {}).get("fresh") or {}
        check("S1-ticket-given", fr.get("ticket") is True and fr.get("image_sha256") == hashlib.sha256(sealed).hexdigest()
              and "POKE-FRESH ticket ok" in (b or {}).get("lines", []), str(fr))
    else:
        print("SKIP S1 — netboot/root has no start4cd.elf/fixup4cd.dat (git-ignored firmware); refusal cases still run")
        good_img, good_sig = seal.seal(kernel, root=ROOT)

    # S2 — no boot.sig
    b = report(offer(kernel, bootimg=good_img))
    check("S2-no-signature-refused", refused(b, "no boot.sig"), str((b or {}).get("refused")))

    # S3 — one byte of our image inside boot.img changed, signature kept
    bad = bytearray(good_img); i = bytes(good_img).find(kernel[256:288]); bad[i + 5] ^= 0x01
    b = report(offer(kernel, bytes(bad), good_sig))
    check("S3-one-byte-changed-refused", i > 0 and refused(b, "boot.sig names a different boot.img"), str((b or {}).get("refused")))

    # S4 — an image the hub did not make (it drives an output), signed by another key
    gate = seal.pack(open(write_img, "rb").read(), root=ROOT)
    b = report(offer(open(write_img, "rb").read(), gate, seal.boot_sig(gate, key=ATTACKER)))
    check("S4-other-key-refused", refused(b, "signature is not by the trusted key"), str((b or {}).get("refused")))

    # S5 — tampered boot.img, hash line recomputed, the hub's signature kept
    forged = f"{hashlib.sha256(gate).hexdigest()}\n" + "".join(l + "\n" for l in good_sig.splitlines()[1:])
    b = report(offer(open(write_img, "rb").read(), gate, forged))
    check("S5-hash-line-forged-refused", refused(b, "signature is not by the trusted key"), str((b or {}).get("refused")))

    # S6 — only a plain sentence.img on offer: a signed-boot node does not ask for it
    mark = os.path.getsize(os.path.join(TMP, "procs.log"))
    before = offer(open(write_img, "rb").read())
    time.sleep(3.0)
    f = open(os.path.join(TMP, "procs.log")); f.seek(mark)
    asked = [l for l in f.read().splitlines() if "[bootloader]" not in l and ("sentence.img" in l or "kernel8.img" in l)]   # the server's side
    check("S6-plain-image-never-asked", report(before, wait_s=0.5) is None and os.path.exists(P) and not asked, f"asked={asked[:2]}")
    os.remove(P)

    # S7 — same image, same boot.img bytes
    check("S7-same-image-same-bootimg", seal.pack(kernel, root=ROOT) == seal.pack(kernel, root=ROOT))

    # S8 — the Pi's own tooling would accept boot.sig
    sigf, imgf = os.path.join(TMP, "sig.bin"), os.path.join(TMP, "boot.img")
    open(sigf, "wb").write(seal.parse_sig(good_sig)["rsa2048"]); open(imgf, "wb").write(good_img)
    r = subprocess.run(["openssl", "dgst", "-sha256", "-verify", PUB, "-signature", sigf, imgf], capture_output=True, text=True)
    check("S8-openssl-verifies-boot-sig", r.returncode == 0, r.stdout.strip() or r.stderr.strip()[:100])

    def ran_nothing(b, why):
        """booted (the signature was fine), asked for a ticket, got none: no op result, no bus traffic, off"""
        r, f = (b or {}).get("result") or {}, (b or {}).get("fresh") or {}
        return (bool(b) and not b.get("refused") and b.get("powered_off") and set(r) == {"error"}
                and f.get("asked") and not f.get("ticket") and why in str(f.get("hub")) and ((b.get("effects") or {}).get("n") == 0))

    if FIRMWARE:
        # S9 — the image that just ran, sent again
        b = report(offer(kernel, good_img, good_sig))
        check("S9-replayed-image-runs-nothing", ran_nothing(b, "already had its ticket"),
              f"{(b or {}).get('result')} hub={((b or {}).get('fresh') or {}).get('hub')}")
        # S10 — a gate image the hub signed some other time (yesterday's night role), sent while the hub
        # means the read image (pended, not yet sent)
        gimg, gsig = seal.seal(open(write_img, "rb").read(), root=ROOT)
        hubd.intend(ROOT, good_img)
        b = report(offer(open(write_img, "rb").read(), gimg, gsig))
        check("S10-old-signed-gate-image-moves-nothing", ran_nothing(b, "not the image the hub means")
              and 3 not in ((b or {}).get("truth") or {}).get("leds_on", [3]),
              f"{(b or {}).get('result')} hub={((b or {}).get('fresh') or {}).get('hub')} truth={(b or {}).get('truth')}")

    # S11 — the node's own check, on the twin directly (the hub's desk replaced by an attacker's answers)
    st = seal.stamp(open(read_img, "rb").read(), PUB, wait_ms=800)
    stp = os.path.join(TMP, "stamped.img"); open(stp, "wb").write(st)
    shutil.copy(read_img[:-4] + ".elf", stp[:-4] + ".elf")
    seen = []
    def desk(answer):
        def ask(nonce, h):
            seen.append((nonce, h)); return answer(nonce, h)
        return ask
    good = say.boot(stp, say.default_bench(), ticket=desk(lambda n, h: (seal.ticket(n, h), "ticket")))
    old_nonce, old_h = seen[-1]
    cases = {
        "recorded-ticket":  lambda n, h: (seal.ticket(old_nonce, h), "an earlier boot's ticket"),
        "other-key":        lambda n, h: (seal.ticket(n, h, key=ATTACKER), "signed by someone else"),
        "garbage":          lambda n, h: (b"POKE-TICKET " + b"00" * 256 + b"\n", "zeros"),
        "silence":          None,
    }
    outs = {}
    for k, ans in cases.items():
        if ans is None:                                  # nobody answers: the wire is cut
            outs[k] = say.boot(stp, say.default_bench(), ticket=desk(lambda n, h: (b"", "silence")))
        else:
            outs[k] = say.boot(stp, say.default_bench(), ticket=desk(ans))
    check("S11-valid-ticket-runs", ((good.get("result") or {}).get("mC") is not None) and good["powered_off"], str(good.get("result")))
    gst = os.path.join(TMP, "gate-stamped.img"); open(gst, "wb").write(seal.stamp(open(write_img, "rb").read(), PUB, wait_ms=800))
    gb = say.boot(gst, say.default_bench(), ticket=desk(lambda n, h: (seal.ticket(n, h), "ticket")), check_wipe=False)
    check("S11-valid-ticket-gate-moves", 3 in (gb.get("truth") or {}).get("leds_on", []),     # S10's control: the same gate, wanted now
          f"{gb.get('result')} truth={gb.get('truth')}")
    for k, b in outs.items():
        r = b.get("result") or {}
        check(f"S11-{k}-runs-nothing", set(r) == {"error"} and b["powered_off"] and (b.get("effects") or {}).get("n") == 0
              and "POKE-FRESH ticket ok" not in b["lines"], str(r))
    check("S11-wipe-after-refusal", (outs["recorded-ticket"].get("wipe_check") or {}).get("nonzero") == 0,
          str(outs["recorded-ticket"].get("wipe_check")))

    # S12 — every boot a new nonce; the node names the image the hub stamped
    nonces = {n for n, _ in seen}
    check("S12-nonce-new-every-boot", len(nonces) == len(seen) >= 5 and bytes(16) not in nonces, f"{len(nonces)}/{len(seen)}")
    check("S12-node-names-stamped-image", {h for _, h in seen} == {hashlib.sha256(st).digest(), hashlib.sha256(open(gst, "rb").read()).digest()})
    check("S12-same-image-same-stamp", seal.stamp(open(read_img, "rb").read(), PUB, wait_ms=800) == st
          and seal.seal(kernel, root=ROOT)[0] == seal.seal(kernel, root=ROOT)[0])
finally:
    vboot.terminate(); tftpd.terminate()
    for p in (vboot, tftpd):
        try: p.wait(3)
        except subprocess.TimeoutExpired: p.kill(); p.wait()
    log.close()
    if ok != n:
        print(open(os.path.join(TMP, "procs.log")).read()[-3000:])
    shutil.rmtree(TMP, True)

print(f"signed boot {'PASS' if ok == n else 'FAIL'} {ok}/{n}")
sys.exit(0 if ok == n else 1)
