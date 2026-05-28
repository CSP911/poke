ENTRY(_start)

SECTIONS {
    . = 0x1000;

    .text : {
        *(.text)
    }

    .rodata : {
        *(.rodata*)
    }

    .data : {
        *(.data)
    }

    __bss_start = .;
    .bss : {
        *(.bss)
        *(COMMON)
    }
    __bss_end = .;
}
