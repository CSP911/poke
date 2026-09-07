/* Burst worker layout: worker() at offset 0, PC-relative only.
 * No .data/.bss — parameters arrive as patched immediates. */
ENTRY(worker)
SECTIONS
{
    . = 0;
    .text : {
        *(.text.main)
        *(.text*)
    }
    .rodata : { *(.rodata*) }
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) *(.data*) *(.bss*) }
}
