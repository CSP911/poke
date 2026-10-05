# Resident driver written and verified by AI incubation (see device.json "incubated"). Injected with `poke resident i2c-rtc-68`.
CC      = aarch64-elf-gcc
LD      = aarch64-elf-ld
OBJCOPY = aarch64-elf-objcopy
CFLAGS  = -ffreestanding -nostdlib -fno-builtin -fno-stack-protector -Os -Wall \
          -mstrict-align -mcpu=cortex-a72 -ffunction-sections -I../i2c

all: resident.bin

resident.bin: i2c-rtc-68.c ../i2c/i2c_bus.h ../resident.ld ../../../kernel/pi4/poke_api.h
	$(CC) $(CFLAGS) -c -o i2c-rtc-68.o i2c-rtc-68.c
	$(LD) -T ../resident.ld -nostdlib -o i2c-rtc-68.elf i2c-rtc-68.o
	$(OBJCOPY) -O binary i2c-rtc-68.elf resident.bin
	@echo "resident.bin: $$(wc -c < resident.bin | tr -d ' ') bytes"

clean:
	rm -f *.o *.elf resident.bin
