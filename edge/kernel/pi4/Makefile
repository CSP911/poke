CC = aarch64-elf-gcc
LD = aarch64-elf-ld
OBJCOPY = aarch64-elf-objcopy

# -mstrict-align is REQUIRED: MMU is off, so all RAM is Device memory
# and any unaligned load/store raises an alignment fault (DFSC 0x21)
CFLAGS = -ffreestanding -nostdlib -fno-builtin -fno-stack-protector -O1 -Wall -mcpu=cortex-a72 -mstrict-align
LDFLAGS = -T linker.ld -nostdlib

# Pi 4 GPU firmware (downloaded once)
BOOT_DIR = boot
FW_URL = https://github.com/raspberrypi/firmware/raw/master/boot

all: kernel8.img

start.o: start.S
	$(CC) $(CFLAGS) -c -o start.o start.S

kernel.o: kernel.c
	$(CC) $(CFLAGS) -c -o kernel.o kernel.c

poke-pi4.elf: start.o kernel.o
	$(LD) $(LDFLAGS) -o poke-pi4.elf start.o kernel.o

kernel8.img: poke-pi4.elf
	$(OBJCOPY) -O binary poke-pi4.elf kernel8.img
	@echo "Kernel size: $$(wc -c < kernel8.img | tr -d ' ') bytes"

# ── QEMU twin build (no GENET, built-in demo persona) ──
kernel8-qemu.img: start.S kernel.c linker.ld
	$(CC) $(CFLAGS) -DNO_ETH -c -o start.qemu.o start.S
	$(CC) $(CFLAGS) -DNO_ETH -c -o kernel.qemu.o kernel.c
	$(LD) $(LDFLAGS) -o poke-pi4-qemu.elf start.qemu.o kernel.qemu.o
	$(OBJCOPY) -O binary poke-pi4-qemu.elf kernel8-qemu.img
	@echo "QEMU twin kernel: $$(wc -c < kernel8-qemu.img | tr -d ' ') bytes"

qemu: kernel8-qemu.img
	qemu-system-aarch64 -M raspi4b -kernel kernel8-qemu.img -serial stdio

# ── Firmware download (one-time) ──
$(BOOT_DIR)/start4.elf:
	@mkdir -p $(BOOT_DIR)
	@echo "Downloading Pi 4 firmware..."
	curl -sL -o $@ $(FW_URL)/start4.elf

$(BOOT_DIR)/fixup4.dat:
	@mkdir -p $(BOOT_DIR)
	curl -sL -o $@ $(FW_URL)/fixup4.dat

$(BOOT_DIR)/bcm2711-rpi-4-b.dtb:
	@mkdir -p $(BOOT_DIR)
	curl -sL -o $@ $(FW_URL)/bcm2711-rpi-4-b.dtb

# ── poke.img: bootable SD card image ──
# Built by cloning a PROVEN Raspberry Pi OS boot partition (base.img =
# MBR + first FAT32 partition, Linux partition entry zeroed), then
# overwriting config.txt + kernel8.img. Hand-built FAT images (mformat
# superfloppy, mtools/diskutil FAT32) are rejected by the Pi 4
# bootloader with 4 green blinks — do not go back to that.
#
# boot/base.img is created once from the official Pi OS Lite image:
#   curl -L -o raspios.img.xz https://downloads.raspberrypi.com/raspios_lite_arm64_latest
#   xz -d raspios.img.xz
#   dd if=raspios.img of=boot/base.img bs=512 count=$$((16384+1048576))
#   dd if=/dev/zero of=boot/base.img bs=1 seek=462 count=16 conv=notrunc
PART_OFF = 8388608

poke.img: kernel8.img config.txt $(BOOT_DIR)/base.img
	@which mcopy >/dev/null 2>&1 || { echo "Error: mtools required — brew install mtools"; exit 1; }
	@echo "Building poke.img..."
	cp $(BOOT_DIR)/base.img $@
	mcopy -o -i poke.img@@$(PART_OFF) config.txt ::config.txt
	mcopy -o -i poke.img@@$(PART_OFF) kernel8.img ::kernel8.img

$(BOOT_DIR)/base.img:
	@echo "Error: boot/base.img missing — see comment above poke.img target"; exit 1
	@echo ""
	@echo "  poke.img ready ($$(wc -c < $@ | tr -d ' ') bytes)"
	@echo ""
	@echo "  Flash to SD card:"
	@echo "    diskutil list                          # find SD card (e.g. /dev/disk2)"
	@echo "    diskutil unmountDisk /dev/diskN"
	@echo "    sudo dd if=poke.img of=/dev/rdiskN bs=1m"
	@echo "    diskutil eject /dev/diskN"
	@echo ""
	@echo "  Then connect ethernet to Mac (10.0.0.1/24)"
	@echo "  Pi 4 IP: 10.0.0.2 UDP port 5555"

# ── Flash to SD card (macOS) ──
install: poke.img
	@echo "Insert SD card, then:"
	@echo "  diskutil list"
	@echo "  diskutil unmountDisk /dev/diskN"
	@echo "  sudo dd if=poke.img of=/dev/rdiskN bs=1m"
	@echo "  diskutil eject /dev/diskN"

clean:
	rm -f *.o *.elf *.img

distclean: clean
	rm -rf $(BOOT_DIR)

.PHONY: all install clean distclean
