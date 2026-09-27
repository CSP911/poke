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
    .align 7                /* 8: lower EL, a64: sync — SVC from a unit at EL0 */
    b       el0_sync_entry
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

/* ── EL0 units: exception entry, enter, exit ──
 * A unit runs at EL0 in its own address space. SVC (and faults) from EL0
 * land here on SP_EL1, which is the kernel stack as proc_enter left it.
 * Saved frame (256 bytes): x0-x18 pairs, x29,x30, elr, spsr. */
el0_sync_entry:
    sub     sp, sp, #256
    stp     x0, x1, [sp, #0]
    stp     x2, x3, [sp, #16]
    stp     x4, x5, [sp, #32]
    stp     x6, x7, [sp, #48]
    stp     x8, x9, [sp, #64]
    stp     x10, x11, [sp, #80]
    stp     x12, x13, [sp, #96]
    stp     x14, x15, [sp, #112]
    stp     x16, x17, [sp, #128]
    stp     x18, x29, [sp, #144]
    str     x30, [sp, #160]
    mrs     x9, elr_el1
    mrs     x10, spsr_el1
    stp     x9, x10, [sp, #176]
    mrs     x0, esr_el1
    mrs     x1, far_el1
    mov     x2, sp
    bl      el0_sync                    /* C: syscall (returns) or unit kill (never returns) */
    ldp     x9, x10, [sp, #176]
    msr     elr_el1, x9
    msr     spsr_el1, x10
    ldp     x0, x1, [sp, #0]
    ldp     x2, x3, [sp, #16]
    ldp     x4, x5, [sp, #32]
    ldp     x6, x7, [sp, #48]
    ldp     x8, x9, [sp, #64]
    ldp     x10, x11, [sp, #80]
    ldp     x12, x13, [sp, #96]
    ldp     x14, x15, [sp, #112]
    ldp     x16, x17, [sp, #128]
    ldp     x18, x29, [sp, #144]
    ldr     x30, [sp, #160]
    add     sp, sp, #256
    eret

/* u64 proc_enter(entry, sp_el0, ttbr0): switch to the unit's tables and
 * drop to EL0. Returns (via proc_exit) with the unit's exit code. */
.global proc_enter
proc_enter:
    adr     x9, kctx
    stp     x19, x20, [x9, #0]
    stp     x21, x22, [x9, #16]
    stp     x23, x24, [x9, #32]
    stp     x25, x26, [x9, #48]
    stp     x27, x28, [x9, #64]
    stp     x29, x30, [x9, #80]
    mov     x10, sp
    str     x10, [x9, #96]
    msr     ttbr0_el1, x2
    tlbi    vmalle1
    dsb     ish
    isb
    msr     sp_el0, x1
    msr     elr_el1, x0
    mov     x9, #0x3c0                  /* SPSR: EL0t, D A I F masked (no IRQs yet) */
    msr     spsr_el1, x9
    eret

/* void proc_exit(u64 code): abandon the unit, resume after proc_enter. */
.global proc_exit
proc_exit:
    adr     x9, kctx
    ldp     x19, x20, [x9, #0]
    ldp     x21, x22, [x9, #16]
    ldp     x23, x24, [x9, #32]
    ldp     x25, x26, [x9, #48]
    ldp     x27, x28, [x9, #64]
    ldp     x29, x30, [x9, #80]
    ldr     x10, [x9, #96]
    mov     sp, x10
    ret

.section .bss
.align 4
kctx:   .skip 128
.section .text
