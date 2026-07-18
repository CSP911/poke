PREFIX = riscv64-elf-
CC = $(PREFIX)gcc
LD = $(PREFIX)ld
OBJCOPY = $(PREFIX)objcopy
QEMU = qemu-system-riscv32

ARCH = -march=rv32im_zicsr_zifencei -mabi=ilp32
CFLAGS = $(ARCH) -ffreestanding -nostdlib -fno-builtin -fno-stack-protector -O1 -Wall
LDFLAGS = -T linker.ld -nostdlib -m elf32lriscv

all: poke-rv32.bin

start.o: start.S
	$(CC) $(CFLAGS) -c -o start.o start.S

kernel.o: kernel.c
	$(CC) $(CFLAGS) -c -o kernel.o kernel.c

poke-rv32.elf: start.o kernel.o
	$(LD) $(LDFLAGS) -o poke-rv32.elf start.o kernel.o

poke-rv32.bin: poke-rv32.elf
	$(OBJCOPY) -O binary poke-rv32.elf poke-rv32.bin

run: poke-rv32.bin
	$(QEMU) -machine virt -cpu rv32 -m 128M \
		-kernel poke-rv32.bin -nographic \
		-bios none

clean:
	rm -f *.o *.elf *.bin

.PHONY: all run clean
