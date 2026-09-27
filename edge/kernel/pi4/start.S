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

    /* ── Privilege: the kernel runs at EL1 (units will run at EL0). The GPU
     * hands us EL2; configure EL2 so EL1 owns the timer, FP/SIMD and the
     * implementation-defined registers, then eret down. A kernel reload
     * arrives here already at EL1 with the MMU possibly on — start clean. */
#ifdef POKE_EL1
    mrs     x1, CurrentEL
    lsr     x1, x1, #2
    cmp     x1, #2
    b.ne    el1_entry_check
    mov     x0, #(1 << 31)              /* HCR_EL2.RW: EL1 is AArch64 */
    msr     hcr_el2, x0
    mov     x0, #3                      /* CNTHCTL_EL2: EL1PCTEN|EL1PCEN — EL1 sees the physical timer */
    msr     cnthctl_el2, x0
    msr     cntvoff_el2, xzr
    mov     x0, #0x73                   /* ACTLR_EL2: EL1 may write CPUACTLR/CPUECTLR/L2CTLR/L2ECTLR */
    msr     actlr_el2, x0
    mov     x0, #0x33ff                 /* CPTR_EL2: no FP/SIMD traps to EL2 */
    msr     cptr_el2, x0
    msr     hstr_el2, xzr
    mov     x0, #(3 << 20)              /* CPACR_EL1: FP/SIMD enabled at EL1/EL0 */
    msr     cpacr_el1, x0
    ldr     x0, =0x30d00800             /* SCTLR_EL1: RES1 only — MMU/caches off until mmu_init */
    msr     sctlr_el1, x0
    ldr     x0, =vectors
    msr     vbar_el1, x0
    mov     x0, #0x3c5                  /* SPSR_EL2: D A I F masked, EL1h */
    msr     spsr_el2, x0
    adr     x0, el1_entry
    msr     elr_el2, x0
    eret

el1_entry_check:
    /* already EL1 (kernel reload): make sure MMU/caches are off */
    mrs     x0, sctlr_el1
    bic     x0, x0, #(1 << 0)
    bic     x0, x0, #(1 << 2)
    bic     x0, x0, #(1 << 12)
    msr     sctlr_el1, x0
    isb
    tlbi    vmalle1
    ic      iallu
    dsb     sy
    isb
el1_entry:
    ldr     x0, =0x01000000             /* SP_EL1 */
    mov     sp, x0
    ldr     x0, =vectors
    msr     vbar_el1, x0

#endif
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

/* ── Kernel reload trampoline ──
 * Copied out of the image (to 0x00700000) before use, because it overwrites
 * the image it came from. x0 = staged kernel, x1 = length. Copies to 0x80000
 * and restarts at _start, as if the GPU had just loaded a new kernel8.img. */
.global reload_tramp
.global reload_tramp_end
reload_tramp:
    mov     x2, #0x80000
    mov     x3, x2
    add     x1, x1, #8
1:  cmp     x1, #0
    b.le    2f
    ldr     x4, [x0], #8
    str     x4, [x2], #8
    sub     x1, x1, #8
    b       1b
2:  dsb     sy
    ic      iallu
    dsb     sy
    isb
    br      x3
reload_tramp_end:
