# Resident driver: Pi 4 USB host (PCIe/VL805 xHCI/hub) → eGalax HID touch.
# Injected at runtime with `poke resident usb-touch-egalax`; never linked into the kernel.
CC      = aarch64-elf-gcc
LD      = aarch64-elf-ld
OBJCOPY = aarch64-elf-objcopy
CFLAGS  = -ffreestanding -nostdlib -fno-builtin -fno-stack-protector -Os -Wall \
          -mstrict-align -mcpu=cortex-a72 -ffunction-sections

all: resident.bin

resident.bin: usb_touch.c ../resident.ld ../../../kernel/pi4/poke_api.h
	$(CC) $(CFLAGS) -c -o usb_touch.o usb_touch.c
	$(LD) -T ../resident.ld -nostdlib -o usb_touch.elf usb_touch.o
	$(OBJCOPY) -O binary usb_touch.elf resident.bin
	@echo "resident.bin: $$(wc -c < resident.bin | tr -d ' ') bytes"

clean:
	rm -f usb_touch.o usb_touch.elf resident.bin
