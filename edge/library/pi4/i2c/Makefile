# Resident driver: Pi 4 I2C bus (BSC1, GPIO 2/3). Injected with `poke resident i2c`.
CC      = aarch64-elf-gcc
LD      = aarch64-elf-ld
OBJCOPY = aarch64-elf-objcopy
CFLAGS  = -ffreestanding -nostdlib -fno-builtin -fno-stack-protector -Os -Wall \
          -mstrict-align -mcpu=cortex-a72 -ffunction-sections

all: resident.bin

resident.bin: i2c.c ../resident.ld ../../../kernel/pi4/poke_api.h
	$(CC) $(CFLAGS) -c -o i2c.o i2c.c
	$(LD) -T ../resident.ld -nostdlib -o i2c.elf i2c.o
	$(OBJCOPY) -O binary i2c.elf resident.bin
	@echo "resident.bin: $$(wc -c < resident.bin | tr -d ' ') bytes"

probe: i2cprobe.c ../unit.ld
	$(CC) $(CFLAGS) -c -o i2cprobe.o i2cprobe.c
	$(LD) -T ../unit.ld -nostdlib -o i2cprobe.elf i2cprobe.o
	$(OBJCOPY) -O binary i2cprobe.elf i2cprobe.bin

clean:
	rm -f *.o *.elf resident.bin i2cprobe.bin
