# Sentence image runtime (v1: the op runs at EL0 behind pt.h). The hub (sentence/say.py) builds
# images itself; this Makefile only checks the runtime compiles, builds the hello op, and runs the
# off-target fence test on it. Runtime objects must be named poke_rt_*.o (image.ld places them by name).
CC = aarch64-elf-gcc
CFLAGS = -ffreestanding -nostdlib -fno-builtin -fno-stack-protector -O2 -Wall -mcpu=cortex-a72 -mstrict-align -mgeneral-regs-only -fno-jump-tables -I. -Ifrags
RTFLAGS = $(CFLAGS) -Os
hello.img: boot.S rt.c rt.h pt.h rt_power.c rt_power.h rt_fresh.c rt_fresh.h ustub.S image.ld hello.c pt_test.c
	$(CC) $(RTFLAGS) -c boot.S -o poke_rt_boot.o
	$(CC) $(RTFLAGS) -c rt.c -o poke_rt_main.o
	$(CC) $(RTFLAGS) -c rt_power.c -o poke_rt_power.o
	$(CC) $(RTFLAGS) -c rt_fresh.c -o poke_rt_fresh.o
	$(CC) $(RTFLAGS) -c ustub.S -o ustub.o
	$(CC) $(CFLAGS) -c hello.c -o hello.o
	aarch64-elf-ld -T image.ld --no-warn-rwx-segments --orphan-handling=error -o hello.elf poke_rt_boot.o poke_rt_main.o poke_rt_power.o poke_rt_fresh.o ustub.o hello.o
	aarch64-elf-objcopy -O binary hello.elf hello.img
	cc -O1 -Wall -o pt_test pt_test.c
	aarch64-elf-nm hello.elf | ./pt_test | tail -1
	@echo "hello.img: $$(wc -c < hello.img | tr -d ' ') bytes"
clean:
	rm -f *.o *.elf *.img pt_test
