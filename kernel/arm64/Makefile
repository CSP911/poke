CC = aarch64-elf-gcc
LD = aarch64-elf-ld
OBJCOPY = aarch64-elf-objcopy
QEMU = qemu-system-aarch64

CFLAGS = -ffreestanding -nostdlib -fno-builtin -fno-stack-protector -O0 -Wall
LDFLAGS = -T linker.ld -nostdlib

all: poke-arm.bin

start.o: start.S
	$(CC) $(CFLAGS) -c -o start.o start.S

kernel.o: kernel.c
	$(CC) $(CFLAGS) -c -o kernel.o kernel.c

poke-arm.elf: start.o kernel.o
	$(LD) $(LDFLAGS) -o poke-arm.elf start.o kernel.o

poke-arm.bin: poke-arm.elf
	$(OBJCOPY) -O binary poke-arm.elf poke-arm.bin

run: poke-arm.bin
	$(QEMU) -machine virt -cpu cortex-a53 -m 128M \
		-kernel poke-arm.bin -nographic \
		-chardev stdio,id=con -serial chardev:con \
		-chardev socket,id=net,host=0.0.0.0,port=8081,server=on,wait=off -serial chardev:net \
		-chardev file,id=audio,path=/tmp/poke-audio.pcm -serial chardev:audio \
		-monitor none

clean:
	rm -f *.o *.elf *.bin

.PHONY: all run clean
