# Resident driver: Pi 4 SD card (EMMC2 / SDHCI). Injected with `poke resident sdcard`.
CC      = aarch64-elf-gcc
LD      = aarch64-elf-ld
OBJCOPY = aarch64-elf-objcopy
CFLAGS  = -ffreestanding -nostdlib -fno-builtin -fno-stack-protector -Os -Wall \
          -mstrict-align -mcpu=cortex-a72 -ffunction-sections

all: resident.bin

resident.bin: sdcard.c sdhci.h ../resident.ld ../../../kernel/pi4/poke_api.h
	$(CC) $(CFLAGS) -c -o sdcard.o sdcard.c
	$(LD) -T ../resident.ld -nostdlib -o sdcard.elf sdcard.o
	$(OBJCOPY) -O binary sdcard.elf resident.bin
	@echo "resident.bin: $$(wc -c < resident.bin | tr -d ' ') bytes"

# incubation probe (EL0 unit, verbose): run with sim/body/pokeedge.run_unit or `poke unit`
probe: sdprobe.c sdhci.h ../unit.ld
	$(CC) $(CFLAGS) -c -o sdprobe.o sdprobe.c
	$(LD) -T ../unit.ld -nostdlib -o sdprobe.elf sdprobe.o
	$(OBJCOPY) -O binary sdprobe.elf sdprobe.bin

clean:
	rm -f *.o *.elf resident.bin sdprobe.bin
