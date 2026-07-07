CC = aarch64-elf-gcc
LD = aarch64-elf-ld
OBJCOPY = aarch64-elf-objcopy

CFLAGS = -ffreestanding -nostdlib -fno-builtin -fno-stack-protector -O1 -Wall -mcpu=cortex-a72
LDFLAGS = -T linker.ld -nostdlib

all: kernel8.img

start.o: start.S
	$(CC) $(CFLAGS) -c -o start.o start.S

kernel.o: kernel.c
	$(CC) $(CFLAGS) -c -o kernel.o kernel.c

poke-pi4.elf: start.o kernel.o
	$(LD) $(LDFLAGS) -o poke-pi4.elf start.o kernel.o

kernel8.img: poke-pi4.elf
	$(OBJCOPY) -O binary poke-pi4.elf kernel8.img
	@echo "Kernel size: $$(wc -c < kernel8.img) bytes"

# Copy to SD card (macOS)
install: kernel8.img
	@echo "Copying to SD card..."
	cp kernel8.img config.txt /Volumes/NO\ NAME/ 2>/dev/null || \
	cp kernel8.img config.txt /Volumes/boot/ 2>/dev/null || \
	echo "SD card not mounted at /Volumes/NO NAME or /Volumes/boot"
	@echo "Also need: start4.elf, fixup4.dat from /tmp/pi4-boot/"

clean:
	rm -f *.o *.elf *.img

.PHONY: all install clean
