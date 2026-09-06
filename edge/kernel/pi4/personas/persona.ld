/* Persona binary layout: persona_main at offset 0, PC-relative only.
 * No .data/.bss — personas must be stateless (state lives in the hub
 * or is derived from tick/api). */
ENTRY(persona_main)
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
