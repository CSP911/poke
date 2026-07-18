CC = arm-none-eabi-gcc
LD = arm-none-eabi-ld
OBJCOPY = arm-none-eabi-objcopy

CFLAGS = -mcpu=arm1176jzf-s -ffreestanding -nostdlib -fno-builtin -fno-stack-protector -O1 -Wall
LDFLAGS = -T linker.ld -nostdlib

all: kernel.img

start.o: start.S
	$(CC) $(CFLAGS) -c -o start.o start.S

kernel.o: kernel.c
	$(CC) $(CFLAGS) -c -o kernel.o kernel.c

poke-pi0w.elf: start.o kernel.o
	$(LD) $(LDFLAGS) -o poke-pi0w.elf start.o kernel.o

kernel.img: poke-pi0w.elf
	$(OBJCOPY) -O binary poke-pi0w.elf kernel.img
	@echo "Kernel size: $$(wc -c < kernel.img) bytes"

qemu: poke-pi0w-qemu.bin

start_qemu.o: start_qemu.S
	$(CC) $(CFLAGS) -c -o start_qemu.o start_qemu.S

kernel_qemu.o: kernel_qemu.c
	$(CC) $(CFLAGS) -c -o kernel_qemu.o kernel_qemu.c

poke-pi0w-qemu.elf: start_qemu.o kernel_qemu.o
	$(LD) -T linker_qemu.ld -nostdlib -o poke-pi0w-qemu.elf start_qemu.o kernel_qemu.o

poke-pi0w-qemu.bin: poke-pi0w-qemu.elf
	$(OBJCOPY) -O binary poke-pi0w-qemu.elf poke-pi0w-qemu.bin
	@echo "QEMU kernel size: $$(wc -c < poke-pi0w-qemu.bin) bytes"

run-qemu: poke-pi0w-qemu.bin
	qemu-system-arm -M virt -cpu arm1176 -m 128M \
		-kernel poke-pi0w-qemu.bin -nographic

clean:
	rm -f *.o *.elf *.img *.bin

.PHONY: all qemu run-qemu clean
