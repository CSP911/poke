/* POKE sentence image (x86, Multiboot at 1 MiB). Runtime objects are named poke_rt_*.o; everything
 * else (ustub, the op, libgcc helpers) is the op's and lands in its own pages:
 *   [1 MiB, __rt_text_end)        runtime code, read-only data, initialised data   (ring 0 only)
 *   [__op_x_start, __op_x_end)    op code                                           (ring 3, read)
 *   [__op_x_end, __op_r_end)      op read-only data                                 (ring 3, read)
 *   [__op_guard, +4 KiB)          guard — not mapped (stack overflow faults)
 *   [__op_rw_start, __op_rw_end)  op data, bss, stack                               (ring 3, read/write)
 *   [__op_rw_end, __rt_end)       runtime bss, page tables, kernel stack            (ring 0 only)
 * The scrub at the end zeroes [__rt_text_end, __rt_end). */
ENTRY(_start)
SECTIONS
{
    . = 0x100000;
    .multiboot : { KEEP(*(.multiboot)) }
    .rt_text : { *poke_rt_*(.text.boot) *poke_rt_*(.text .text.*) *poke_rt_*(.rodata .rodata.*) *poke_rt_*(.data .data.*) }
    . = ALIGN(4096);
    __rt_text_end = .;
    __op_x_start = .;
    .op_text : { *(.text .text.*) }
    . = ALIGN(4096);
    __op_x_end = .;
    .op_rodata : { *(.rodata .rodata.*) }
    . = ALIGN(4096);
    __op_r_end = .;
    __op_guard = .;
    . += 4096;
    __op_rw_start = .;
    .op_data : { *(EXCLUDE_FILE(*poke_rt_*) .data .data.*) }
    __op_bss_start = .;
    .op_bss (NOLOAD) : { *(EXCLUDE_FILE(*poke_rt_*) .bss .bss.*) *(EXCLUDE_FILE(*poke_rt_*) COMMON) }
    . = ALIGN(16);
    . += 0x4000;
    __op_stack_top = .;
    . = ALIGN(4096);
    __op_rw_end = .;
    .rt_bss (NOLOAD) : { *poke_rt_*(.bss .bss.* COMMON) }
    . = ALIGN(4096);
    .rt_pt (NOLOAD) : { __pt_dir = .; . += 4096; __pt_tab = .; . += 4096; }
    . += 0x2000;
    __kstack_top = .;
    . = ALIGN(4096);
    __rt_end = .;
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
