/* Persona binary layout: persona_main at offset 0, PC-relative only.
 * .data/.bss are KEPT and emitted into the flat binary (bss as zeros), so a
 * persona may hold state in static variables across ticks. It is still
 * volatile: RAM only, reset on injection, gone on stop. */
ENTRY(persona_main)
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
