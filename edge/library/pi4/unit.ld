/* Unit (EL0 process) layout: linked at the fixed unit VA. Every unit sees
 * itself at UNIT_BASE; the kernel maps whatever physical pages it likes there.
 * .data/.bss are emitted into the image (bss as zeros). Entry at offset 0. */
ENTRY(_start)
SECTIONS
{
    . = 0x100000000000;
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
