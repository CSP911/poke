ENTRY(_start)

SECTIONS {
    . = 0x40010000;

    .text.boot : { *(.text.boot) }
    .text : { *(.text) }
    .rodata : { *(.rodata*) }
    .data : { *(.data) }

    . = ALIGN(4);
    __bss_start = .;
    .bss : { *(.bss) *(COMMON) }
    __bss_end = .;
}
