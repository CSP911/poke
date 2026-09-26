/* Resident driver layout: resident_main at offset 0, PC-relative only.
 * Unlike personas, .data/.bss are KEPT — they are emitted into the flat
 * binary (bss as zeros) so the loader needs no separate zeroing step and the
 * image can be dropped at any address as one contiguous block. */
ENTRY(resident_main)
SECTIONS
{
    . = 0;
    .text : {
        *(.text.main)
        *(.text*)
    }
    .rodata : { *(.rodata*) }
    . = ALIGN(16);
    .data : {
        *(.data*)
        . = ALIGN(16);
        *(.bss*)
        *(COMMON)
        . = ALIGN(16);
    }
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
