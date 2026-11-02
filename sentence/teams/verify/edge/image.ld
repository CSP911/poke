/* Sentence image v1. Runtime objects are named poke_rt_*.o; everything else
 * (the op and ustub.o) lands in the op's pages. Every permission boundary is
 * page aligned so pt.h can fence it. The file ends after the op's data; the
 * bss, stacks and page tables after it are not in the image. */
ENTRY(_start)
SECTIONS
{
    . = 0x80000;
    __rt_text_start = .;
    .rt_text : { KEEP(*poke_rt_boot.o(.text.boot)) *poke_rt_*.o(.text*) *poke_rt_*.o(.rodata*) }
    .rt_data : { *poke_rt_*.o(.data*) }
    . = ALIGN(4096);
    __rt_text_end = .;

    .op_text : { *(EXCLUDE_FILE(*poke_rt_*.o) .text*) }
    . = ALIGN(4096);
    __op_x_end = .;
    .op_rodata : { *(EXCLUDE_FILE(*poke_rt_*.o) .rodata*) }
    . = ALIGN(4096);
    __op_r_end = .;
    .op_data : { *(EXCLUDE_FILE(*poke_rt_*.o) .data*) }
    . = ALIGN(16);
    .op_bss (NOLOAD) : { __op_bss_start = .; *(EXCLUDE_FILE(*poke_rt_*.o) .bss*) *(EXCLUDE_FILE(*poke_rt_*.o) COMMON) }
    . = ALIGN(4096);
    __op_guard = .;                 /* unmapped: a stack overflow faults instead of eating bss */
    . = . + 0x1000;
    . = . + 0x4000;
    __op_stack_top = .;
    __op_rw_end = .;

    .rt_bss (NOLOAD) : { *poke_rt_*.o(.bss*) *poke_rt_*.o(COMMON) }
    . = ALIGN(16);
    . = . + 0x4000;
    __rt_stack_top = .;
    . = ALIGN(4096);
    __rt_end = .;

    /* linker bookkeeping a static image never fills; refused if anything does */
    .unused : { *(.iplt) *(.igot.plt) *(.got) *(.got.plt) *(.rela.*) }
    ASSERT(SIZEOF(.unused) == 0, "image needs a GOT/PLT/relocations at run time — not an op")
    ASSERT(SIZEOF(.rt_data) == 0, "runtime keeps no initialised data (it would sit in the op's way)")
    ASSERT(__rt_end <= 0x200000, "image must fit the 2 MB the fence maps")
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
