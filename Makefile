ASM = nasm
CC = i686-elf-gcc
LD = i686-elf-ld
OBJCOPY = i686-elf-objcopy
QEMU = qemu-system-i386

CFLAGS = -ffreestanding -nostdlib -fno-builtin -fno-stack-protector -O0 -Wall -Wno-unused-function -Wno-unused-variable
LDFLAGS = -T linker.ld -nostdlib

all: poke.img

boot.bin: boot.asm
	$(ASM) -f bin -o boot.bin boot.asm

kernel_entry.o: kernel_entry.asm
	$(ASM) -f elf32 -o kernel_entry.o kernel_entry.asm

kernel.o: kernel.c
	$(CC) $(CFLAGS) -c -o kernel.o kernel.c

kernel.elf: kernel_entry.o kernel.o
	$(LD) $(LDFLAGS) -o kernel.elf kernel_entry.o kernel.o

kernel.bin: kernel.elf
	$(OBJCOPY) -O binary kernel.elf kernel.bin

poke.img: boot.bin kernel.bin
	cat boot.bin kernel.bin > poke.img
	dd if=/dev/zero bs=1 count=0 seek=131072 of=poke.img 2>/dev/null

run: poke.img
	$(QEMU) -drive format=raw,file=poke.img -m 64M \
		-vga std \
		-device e1000,netdev=net0 \
		-netdev user,id=net0,hostfwd=tcp::8080-:80 \
		-device virtio-rng-pci \
		-device virtio-balloon-pci \
		-monitor stdio

# 가상 PCI 디바이스 포함 버전
run-devices: poke.img
	$(QEMU) -drive format=raw,file=poke.img -m 64M \
		-vga std \
		-device e1000,netdev=net0 \
		-netdev user,id=net0,hostfwd=tcp::8080-:80 \
		-device virtio-rng-pci \
		-device virtio-balloon-pci \
		-device virtio-serial-pci \
		-device ac97 \
		-monitor stdio

clean:
	rm -f *.bin *.o *.elf poke.img

.PHONY: all run clean
