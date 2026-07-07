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

clean:
	rm -f *.o *.elf *.img

.PHONY: all clean
