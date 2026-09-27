# Hardware check: RAM test probe (parameter-patched per chunk by lib/cli/check.js)
CC = aarch64-elf-gcc
LD = aarch64-elf-ld
OBJCOPY = aarch64-elf-objcopy
CFLAGS = -ffreestanding -nostdlib -fno-builtin -fno-stack-protector -Os -Wall -mstrict-align -mcpu=cortex-a72 -ffunction-sections
all: check.bin
check.bin: memtest.c ../../../kernel/pi4/personas/persona.ld
	$(CC) $(CFLAGS) -c -o memtest.o memtest.c
	$(LD) -T ../../../kernel/pi4/personas/persona.ld -nostdlib -o memtest.elf memtest.o
	$(OBJCOPY) -O binary memtest.elf check.bin
	@echo "check.bin: $$(wc -c < check.bin | tr -d ' ') bytes"
clean:
	rm -f memtest.o memtest.elf check.bin
