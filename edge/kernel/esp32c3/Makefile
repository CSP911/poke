PREFIX = riscv64-elf-
CC = $(PREFIX)gcc
LD = $(PREFIX)ld
OBJCOPY = $(PREFIX)objcopy

ARCH = -march=rv32imc_zicsr_zifencei -mabi=ilp32
CFLAGS = $(ARCH) -ffreestanding -nostdlib -fno-builtin -fno-stack-protector -O1 -Wall
LDFLAGS = -T linker.ld -nostdlib -m elf32lriscv

all: poke-esp32c3.bin

start.o: start.S
	$(CC) $(CFLAGS) -c -o start.o start.S

kernel.o: kernel.c
	$(CC) $(CFLAGS) -c -o kernel.o kernel.c

poke-esp32c3.elf: start.o kernel.o
	$(LD) $(LDFLAGS) -o poke-esp32c3.elf start.o kernel.o \
		
		

poke-esp32c3.bin: poke-esp32c3.elf
	$(OBJCOPY) -O binary poke-esp32c3.elf poke-esp32c3.bin
	@echo "Binary size: $$(wc -c < poke-esp32c3.bin) bytes"

flash: poke-esp32c3.bin
	esptool.py --chip esp32c3 -p /dev/cu.usbmodem* -b 460800 \
		write_flash --flash_mode dio --flash_size 4MB 0x0 poke-esp32c3.bin

clean:
	rm -f *.o *.elf *.bin

.PHONY: all flash clean
