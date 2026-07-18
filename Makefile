ASM = nasm
CC = i686-elf-gcc
LD = i686-elf-ld
OBJCOPY = i686-elf-objcopy
QEMU = qemu-system-i386

KERNEL_DIR = kernel/x86
BUILD_DIR = build
CFLAGS = -ffreestanding -nostdlib -fno-builtin -fno-stack-protector -Os -Wall -Wno-unused-function -Wno-unused-variable
LDFLAGS = -T $(KERNEL_DIR)/linker.ld -nostdlib

all: poke.img

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(BUILD_DIR)/boot.bin: $(KERNEL_DIR)/boot.asm | $(BUILD_DIR)
	$(ASM) -f bin -o $@ $<

$(BUILD_DIR)/kernel_entry.o: $(KERNEL_DIR)/kernel_entry.asm | $(BUILD_DIR)
	$(ASM) -f elf32 -o $@ $<

$(BUILD_DIR)/kernel.o: $(KERNEL_DIR)/kernel.c | $(BUILD_DIR)
	$(CC) $(CFLAGS) -c -o $@ $<

$(BUILD_DIR)/kernel.elf: $(BUILD_DIR)/kernel_entry.o $(BUILD_DIR)/kernel.o
	$(LD) $(LDFLAGS) -o $@ $^

$(BUILD_DIR)/kernel.bin: $(BUILD_DIR)/kernel.elf
	$(OBJCOPY) -O binary $< $@

poke.img: $(BUILD_DIR)/boot.bin $(BUILD_DIR)/kernel.bin
	cat $^ > poke.img
	dd if=/dev/zero bs=1 count=0 seek=131072 of=poke.img 2>/dev/null

run: poke.img
	$(QEMU) -drive format=raw,file=poke.img -m 64M \
		-vga std \
		-device e1000,netdev=net0 \
		-netdev user,id=net0,hostfwd=tcp::8080-:80 \
		-device virtio-rng-pci \
		-device virtio-balloon-pci \
		-monitor stdio

run-audio: poke.img
	QEMU_AUDIO_DRV=coreaudio $(QEMU) -drive format=raw,file=poke.img -m 64M \
		-vga std \
		-device e1000,netdev=net0 \
		-netdev user,id=net0,hostfwd=tcp::8080-:80 \
		-device virtio-rng-pci \
		-device virtio-balloon-pci \
		-audiodev coreaudio,id=audio0 \
		-machine pcspk-audiodev=audio0 \
		-monitor stdio

clean:
	rm -rf $(BUILD_DIR) poke.img

.PHONY: all run run-audio clean
