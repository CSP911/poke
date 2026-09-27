CC = aarch64-elf-gcc
LD = aarch64-elf-ld
OBJCOPY = aarch64-elf-objcopy
CFLAGS = -ffreestanding -nostdlib -fno-builtin -fno-stack-protector -O1 -Wall -mstrict-align -mcpu=cortex-a72 -ffunction-sections
all: unit.bin
unit.bin: hello.c ../unit.ld
	$(CC) $(CFLAGS) -c -o hello.o hello.c
	$(LD) -T ../unit.ld -nostdlib -o hello.elf hello.o
	$(OBJCOPY) -O binary hello.elf unit.bin
	@echo "unit.bin: $$(wc -c < unit.bin | tr -d ' ') bytes"
clean:
	rm -f hello.o hello.elf unit.bin
