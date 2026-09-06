/* ============================================
 * POKE OS — Raspberry Pi 4 (BCM2711) Entry
 * ARM64 (AArch64) bare-metal
 *
 * GPU loads kernel8.img to 0x80000, then
 * releases CPU0 to start executing here.
 * ============================================ */

.section .text.boot
.global _start

_start:
    /* Only run on CPU 0, halt others */
    mrs     x0, mpidr_el1
    and     x0, x0, #3
    cbnz    x0, halt

    /* Setup stack at 16MB */
    ldr     x0, =0x01000000
    mov     sp, x0

    /* Install exception vectors for the current EL (EL2 or EL1) */
    ldr     x0, =vectors
    mrs     x1, CurrentEL
    lsr     x1, x1, #2
    cmp     x1, #2
    b.eq    3f
    msr     vbar_el1, x0
    b       4f
3:  msr     vbar_el2, x0
4:

    /* Zero BSS */
    ldr     x0, =__bss_start
    ldr     x1, =__bss_end
1:  cmp     x0, x1
    b.ge    2f
    str     xzr, [x0], #8
    b       1b
2:

    /* Jump to C */
    bl      kernel_main

halt:
    wfe
    b       halt

/* ── Exception vectors ──
 * Every entry funnels into fault_handler(kind, esr, elr, far).
 * No return — the handler reports and parks the CPU. */
.macro VEC kind
    .align 7
    mov     x0, #\kind
    b       fault_entry
.endm

.align 11
.global vectors
vectors:
    VEC 0   /* curr EL, SP0: sync */
    VEC 1   /* curr EL, SP0: irq */
    VEC 2   /* curr EL, SP0: fiq */
    VEC 3   /* curr EL, SP0: serror */
    VEC 4   /* curr EL, SPx: sync */
    VEC 5   /* curr EL, SPx: irq */
    VEC 6   /* curr EL, SPx: fiq */
    VEC 7   /* curr EL, SPx: serror */
    VEC 8   /* lower EL, a64: sync */
    VEC 9   /* lower EL, a64: irq */
    VEC 10  /* lower EL, a64: fiq */
    VEC 11  /* lower EL, a64: serror */
    VEC 12  /* lower EL, a32: sync */
    VEC 13  /* lower EL, a32: irq */
    VEC 14  /* lower EL, a32: fiq */
    VEC 15  /* lower EL, a32: serror */

fault_entry:
    /* Fresh stack — the faulting context's SP may be corrupt */
    ldr     x9, =0x00F00000
    mov     sp, x9
    mrs     x9, CurrentEL
    lsr     x9, x9, #2
    cmp     x9, #2
    b.eq    5f
    mrs     x1, esr_el1
    mrs     x2, elr_el1
    mrs     x3, far_el1
    b       6f
5:  mrs     x1, esr_el2
    mrs     x2, elr_el2
    mrs     x3, far_el2
6:  bl      fault_handler
7:  wfe
    b       7b
