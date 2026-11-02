# Sentence boot on a real Pi 4 (netboot)

The node keeps no files. Its bootloader (in the Pi's SPI EEPROM — the
manufacturer's firmware) fetches everything from the hub over TFTP each time
it starts, and the hub answers only when a sentence is pending.

**Status: built and tested against the twin only — no Pi has booted this yet.**
`preflight.py` lists what is missing before the first try.

## What "off" means here

After its result line the node **resets** through the PM watchdog — it does
not lose power. It comes back up in its bootloader and waits for the hub:

- none of our code runs any more, and before the reset the runtime zeroes the
  op's pages and its own working memory (results, page tables) — the DRAM
  holds nothing of the sentence;
- but the board is on: SoC, DRAM and the Ethernet PHY idle in the bootloader
  (a few watts, not yet measured).

A real power-off is available: an image patched with `halt=1`
(`rtcfg.py pi IMG halt=1`) sets reset partition 63, and a bootloader with
`WAKE_ON_GPIO=0`, `POWER_OFF_ON_HALT=1` then switches the PMIC outputs off.
The next sentence then needs the Pi's GLOBAL_EN shorted to ground by the hub
(a wake line we do not have yet) and a cold boot. (Waking on GPIO3 would
collide with I2C bus 1, which uses that pin.)

## One-time setup

**1. Bootloader.** On the Pi (any Raspberry Pi OS boot, once): update the
EEPROM to the current default release, then `sudo rpi-eeprom-config --edit`
with the settings in `../teams/hw/eeprom.conf`: `BOOT_ORDER=0xf2` (network,
then retry forever), static addresses `CLIENT_IP=10.0.0.2`,
`SUBNET=255.255.255.0`, `TFTP_IP=10.0.0.1` (with these DHCP is skipped — no
DHCP server on the hub), `BOOT_UART=1` for the first runs, and the boot-time
trims (`DISABLE_HDMI=1`, `NET_INSTALL_ENABLED=0`, `ENABLE_SELF_UPDATE=0`).
Reboot once, then remove the SD card.

**2. Report wire.** Pi GPIO 14 (pin 8, TXD) → USB-serial adapter RX, Pi GND
(pin 6) → adapter GND. 3.3 V adapter only. 115200 8N1.

**3. Network.** Pi Ethernet straight to the Mac's Ethernet adapter, set to
manual `10.0.0.1 / 255.255.255.0`.

**4. Hub.** `root/` holds the firmware and `config.txt`:

| file | why |
|---|---|
| `config.txt` | `kernel=sentence.img` at `0x80000`, 64-bit, UART on; `gpu_mem=16` selects the cut-down firmware; `core_freq=500`/`core_freq_min=500` pin the clock I2C runs from |
| `start4cd.elf`, `fixup4cd.dat` | the cut-down VideoCore firmware, a matched pair (Raspberry Pi OS 2026-06-18): 0.85 MB instead of 2.3 MB over TFTP |
| `start4.elf`, `fixup4.dat`, `bcm2711-rpi-4-b.dtb` | the standard pair (drop `gpu_mem=16` to use it) and the device tree |

```sh
cd sentence
../sim/body/.venv/bin/python netboot/preflight.py build/img/<an image>.img   # what is missing
../sim/body/.venv/bin/python netboot/tftpd.py                                # serve (UDP 69, no root needed on macOS)
```

## A request

```sh
../sim/body/.venv/bin/python say.py --pi "LED 3번 켜줘"
```

The hub writes the sentence's image as `root/pending.img` with the Pi's
settings (`tftpd.py --pend IMG --target pi`: watchdog on, I2C divider for
500 MHz — `say.boot_pi` must pass `--target pi`; without it the image keeps
the twin's settings: no watchdog, I2C at ~333 kHz). The bootloader has been waiting on a request the server parked; the
server answers it at once, then serves the files in the order the bootloader
and firmware ask for them:

```
<serial>/start4.elf (probe) · config.txt · recover4.elf, recovery.elf (not found)
start4cd.elf · fixup4cd.dat · config.txt · bcm2711-rpi-4-b.dtb · overlays/overlay_map.dtb (not found)
cmdline.txt (not found) · armstub8-gic.bin (not found) · sentence.img (probe, then in full)
```

A missing file is answered "not found" at once — never with silence, which
would cost the bootloader its whole `TFTP_FILE_TIMEOUT` (30 s) per file. The
sentence is consumed when the image has been sent in full (the firmware's
probe of it does not count). The node runs it, prints `POKE-RESULT`,
`POKE-END`, `POKE-OFF` on the UART, drains the UART, zeroes this boot's
memory and resets — back in its bootloader, asking again; the server parks
that request until the next sentence.

### Always off, on a Pi

The image's settings block (RCFG, `../rtcfg.py`) turns on the PM watchdog for
the Pi target: armed at boot for 15 s, fed by the runtime on every runtime
call (the op cannot reach the PM page — it is mapped for EL1 only). The
watchdog's maximum is ~16 s, shorter than an op's 20 s budget, so on a Pi the
timer interrupt also ends an op that goes 14 s without a runtime call, and
reports why, before the watchdog would reset the node without a word. On the
twin the watchdog stays off (QEMU resets the moment it is armed); `wdog=2`
tests the 14 s rule there.

## Signed boot — only the hub's images run

The node keeps nothing, so whatever answers its TFTP request decides what it becomes, and TFTP has no
authentication. The hub therefore speaks the Pi 4 bootloader's own secure-boot format (`../seal.py`):

- `boot.img`: a FAT16 ramdisk with `config.txt`, the firmware pair `config.txt` selects, the device tree,
  `sentence.img` and `poke.json` (the image's hash and what it is for).
- `boot.sig`: `sha256 / ts / rsa2048`, an RSA-2048 PKCS#1 v1.5 signature over SHA-256 of `boot.img` by the
  hub's key. This is the format `rpi-eeprom-digest` writes, and `openssl dgst -verify` accepts it (test S8).

`tftpd.py --pend` signs the final bytes, after the RCFG patch, and serves them as `boot.img` and `boot.sig`
next to the plain files. The hub's key is created on first use in `sentence/keys/` (git-ignored, 0600), or
in `POKE_KEYS` if that is set. `../seal.py key` prints its paths and fingerprint.

On the twin, `vboot.py --signed ../keys/hub-rsa2048.pub.pem` behaves like a bootloader with `SIGNED_BOOT=1`:
- it asks only for `boot.sig` and `boot.img`, checks the hash and the signature, and only then opens the ramdisk;
- a refused image is never booted, and its report carries `"refused": <why>`;
- the regression's netboot case runs this way.

On a real Pi there are three steps. Only the last cannot be undone. **None of them has been tried on
hardware yet.** The commands follow the Raspberry Pi secure-boot documentation as we understand it; check
them against the current docs on the first run.

1. **Ramdisk, unsigned** (reversible). Add `boot_ramdisk=1` to the TFTP root's `config.txt`. The bootloader
   then loads `boot.img`, which shows that the Pi accepts our FAT16 ramdisk and finds the firmware and kernel
   inside it.
2. **Signed boot without OTP** (reversible by reflashing the EEPROM). Put `SIGNED_BOOT=1` in the EEPROM
   config. Then build the EEPROM image with the hub's public key
   (`rpi-eeprom-config -c boot.conf -p hub-rsa2048.pub.pem -o pieeprom.upd pieeprom.bin`), sign that image
   with the same key (`rpi-eeprom-digest -i pieeprom.upd -o pieeprom.sig -k hub-rsa2048.pem`), and flash it.
   From then on the bootloader refuses any `boot.img` the hub did not sign.
3. **OTP** (irreversible, owner's decision). Program the key's hash into the SoC's OTP. After that the EEPROM
   can no longer be reflashed to switch the check off.

What to record on the first hardware run: the request order in signed mode (`netboot-times.jsonl`), and
whether FAT16 and the size of `boot.img` (~2.1 MB with the cut-down firmware) are accepted.

**Not solved: replay.** The bootloader checks the signature, not the time. An old signed image (yesterday's
day role, for instance) can be served again and will boot. The twin test shows this as XFAIL (S9). Binding a
boot to this request needs a freshness check from the node's side: the runtime draws a nonce from the
hardware RNG, and the hub answers with a signed ticket for that nonce, role and time window. That is the next
step.

## Tests (twin, no Pi)

| | |
|---|---|
| `tftptest.py IMG` | idle → no answer; pending → files and image; consumed once; parked request answered in ~0.1 s; a repeated request starts one transfer |
| `../teams/hw/t_netboot_order.py [N] [--cd]` | tftpd + `vboot.py` (the bootloader's real request order) → twin: N sentences in a row, order, sizes, one consumption each, power-off |
| `../teams/sign/t_signed_boot.py` | signed boot (tftpd + `vboot.py --signed` → twin): the hub's image boots; missing signature, one changed byte, another key, a forged hash line are refused and never booted; a plain image is never asked for; same image → same `boot.img`; openssl verifies `boot.sig`; replay XFAIL |
| `../teams/hw/t_power.py` | RCFG, budget, the 14 s rule, cdiv reaching the I2C divider, reset partition (0 / 63 with halt), memory zeroed after power-off (QMP `pmemsave`) |

## Not yet measured

The netboot time on hardware. Estimate: cold power-on to the image ~4–7 s
(bootloader + Ethernet link ~3 s, firmware ~1–2 s, TFTP < 1 s); with the
bootloader already waiting on a parked request, sentence → node running
~1.5–3 s; after a sentence, the reset and link renegotiation take ~3–4 s
before the next one can start. `netboot-times.jsonl` records every request
and transfer with timestamps for the first real run.
