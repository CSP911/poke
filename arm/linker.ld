ENTRY(_start)

SECTIONS {
    . = 0x40000000;

    .text.boot : { *(.text.boot) }
    .text : { *(.text) }
    .rodata : { *(.rodata*) }
    .data : { *(.data) }

    . = ALIGN(8);
    __bss_start = .;
    .bss : { *(.bss) *(COMMON) }
    __bss_end = .;
}
