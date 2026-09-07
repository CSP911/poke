# POKE — root convenience wrapper.
# The x86 edge kernel build lives with its source: edge/kernel/x86/Makefile
# (other targets: make -C edge/kernel/<target>)

all run run-audio clean:
	$(MAKE) -C edge/kernel/x86 $@

.PHONY: all run run-audio clean
