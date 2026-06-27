ENTRY(_start)

SECTIONS {
    . = 0x80000000;

    .text.boot : { *(.text.boot) }
    .text : { *(.text*) }
    .rodata : { *(.rodata*) }

    . = ALIGN(4);
    .data : { *(.data*) }

    . = ALIGN(4);
    __bss_start = .;
    .bss : { *(.bss*) *(COMMON) }
    __bss_end = .;

    . = ALIGN(4096);
    _end = .;
}
