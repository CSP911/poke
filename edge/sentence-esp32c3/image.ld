/* ESP32-C3 sentence image, loaded into internal SRAM by the ROM (esptool load_ram), no flash.
 * IRAM 0x40380000.. and DRAM 0x3FC80000.. are the same SRAM seen on two buses: code at IRAM +0x400,
 * data at DRAM +0x10000, far enough apart not to overlap. The scrub zeroes everything of the run:
 * the code (seen through the data bus, [__run_start, __run_end)) and the data/bss/stack. */
ENTRY(_start)
MEMORY {
    iram (rx) : ORIGIN = 0x40380400, LENGTH = 0xF000
    dram (rw) : ORIGIN = 0x3FC90000, LENGTH = 0x10000
}
SECTIONS {
    .text.keep : { KEEP(*(.text.keep)) } > iram
    . = ALIGN(4);
    __code_start = .;
    .text : { *(.text.entry) *(.text .text.*) } > iram
    __code_end = .;
    .data : { *(.rodata .rodata.* .srodata .srodata.*) *(.data .data.* .sdata .sdata.*) } > dram
    .bss (NOLOAD) : { __bss_start = .; *(.bss .bss.* .sbss .sbss.* COMMON) . = ALIGN(4); __bss_end = .; } > dram
    . = ALIGN(16);
    . += 0x2000;
    __stack_top = .;
    __data_run_end = .;
    /* the run, as data-bus addresses: code [IRAM - 0x700000] … end of stack */
    __run_start = __code_start - 0x700000;
    __run_end = __data_run_end;
    /DISCARD/ : { *(.comment) *(.note*) *(.riscv.attributes) *(.eh_frame*) }
}
