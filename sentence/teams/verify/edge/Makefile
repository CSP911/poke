# Sentence image runtime v1 (verify team prototype). Builds the test ops in tests/
# into build/<name>.img against the fenced runtime, plus the off-target page-table test.
# Runtime objects must be named poke_rt_*.o (image.ld places them by name).
CC = aarch64-elf-gcc
CFLAGS = -ffreestanding -nostdlib -fno-builtin -fno-stack-protector -O2 -Wall -mcpu=cortex-a72 -mstrict-align -mgeneral-regs-only -fno-jump-tables -I. -Ifrags
LDFLAGS = -T image.ld --no-warn-rwx-segments --orphan-handling=error
B = build
OPS = $(basename $(notdir $(wildcard tests/*.c)))

all: $(addprefix $(B)/,$(addsuffix .img,$(OPS))) $(B)/bad_loop_2s.img pt_test
	@for f in $(B)/*.img; do printf "%-28s %6s B\n" $$(basename $$f) $$(wc -c < $$f | tr -d ' '); done
	aarch64-elf-nm $(B)/ok_tmp105.elf | ./$(B)/pt_test

$(B)/poke_rt_boot.o: boot.S | $(B)
	$(CC) $(CFLAGS) -c $< -o $@
$(B)/poke_rt_main.o: rt.c rt.h pt.h | $(B)
	$(CC) $(CFLAGS) -c $< -o $@
$(B)/poke_rt_main2s.o: rt.c rt.h pt.h | $(B)
	$(CC) $(CFLAGS) -DOP_BUDGET_MS=2000 -c $< -o $@
$(B)/ustub.o: ustub.S | $(B)
	$(CC) $(CFLAGS) -c $< -o $@
$(B)/%.o: tests/%.c rt.h | $(B)
	$(CC) $(CFLAGS) -c $< -o $@
$(B)/%.img: $(B)/%.o $(B)/poke_rt_boot.o $(B)/poke_rt_main.o $(B)/ustub.o image.ld
	aarch64-elf-ld $(LDFLAGS) -o $(B)/$*.elf $(B)/poke_rt_boot.o $(B)/poke_rt_main.o $(B)/ustub.o $<
	aarch64-elf-objcopy -O binary $(B)/$*.elf $@
# the endless-loop case with a 2 s budget, so the twin test is quick
$(B)/bad_loop_2s.img: $(B)/bad_loop.o $(B)/poke_rt_boot.o $(B)/poke_rt_main2s.o $(B)/ustub.o image.ld
	aarch64-elf-ld $(LDFLAGS) -o $(B)/bad_loop_2s.elf $(B)/poke_rt_boot.o $(B)/poke_rt_main2s.o $(B)/ustub.o $<
	aarch64-elf-objcopy -O binary $(B)/bad_loop_2s.elf $@

pt_test: pt_test.c pt.h | $(B)
	cc -O1 -Wall -o $(B)/pt_test pt_test.c
$(B):
	mkdir -p $(B)
clean:
	rm -rf $(B)
.PHONY: all clean pt_test
.SECONDARY:
