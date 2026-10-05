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
    .align 7                /* 9: lower EL, a64: irq — timer slice expired while a unit ran */
    b       el0_irq_entry
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
 * Saved frame (816 bytes): x0-x30 @0, elr/spsr @248, sp_el0 @264,
 * q0-q31 @288 (skills compute in floating point; a preempted unit must get
 * its vector registers back), fpcr/fpsr @800. */
el0_sync_entry:
    sub     sp, sp, #816
    stp     x0, x1, [sp, #0]
    stp     x2, x3, [sp, #16]
    stp     x4, x5, [sp, #32]
    stp     x6, x7, [sp, #48]
    stp     x8, x9, [sp, #64]
    stp     x10, x11, [sp, #80]
    stp     x12, x13, [sp, #96]
    stp     x14, x15, [sp, #112]
    stp     x16, x17, [sp, #128]
    stp     x18, x19, [sp, #144]
    stp     x20, x21, [sp, #160]
    stp     x22, x23, [sp, #176]
    stp     x24, x25, [sp, #192]
    stp     x26, x27, [sp, #208]
    stp     x28, x29, [sp, #224]
    str     x30, [sp, #240]
    mrs     x9, elr_el1
    mrs     x10, spsr_el1
    stp     x9, x10, [sp, #248]
    mrs     x9, sp_el0
    str     x9, [sp, #264]
    stp     q0, q1, [sp, #288]
    stp     q2, q3, [sp, #320]
    stp     q4, q5, [sp, #352]
    stp     q6, q7, [sp, #384]
    stp     q8, q9, [sp, #416]
    stp     q10, q11, [sp, #448]
    stp     q12, q13, [sp, #480]
    stp     q14, q15, [sp, #512]
    stp     q16, q17, [sp, #544]
    stp     q18, q19, [sp, #576]
    stp     q20, q21, [sp, #608]
    stp     q22, q23, [sp, #640]
    stp     q24, q25, [sp, #672]
    stp     q26, q27, [sp, #704]
    stp     q28, q29, [sp, #736]
    stp     q30, q31, [sp, #768]
    mrs     x9, fpcr
    mrs     x10, fpsr
    str     x9, [sp, #800]
    str     x10, [sp, #808]
    mrs     x0, esr_el1
    mrs     x1, far_el1
    mov     x2, sp
    bl      el0_sync                    /* C: syscall (returns) or leaves via proc_exit */
    ldp     x9, x10, [sp, #248]
    msr     elr_el1, x9
    msr     spsr_el1, x10
    ldr     x9, [sp, #800]
    ldr     x10, [sp, #808]
    msr     fpcr, x9
    msr     fpsr, x10
    ldp     q0, q1, [sp, #288]
    ldp     q2, q3, [sp, #320]
    ldp     q4, q5, [sp, #352]
    ldp     q6, q7, [sp, #384]
    ldp     q8, q9, [sp, #416]
    ldp     q10, q11, [sp, #448]
    ldp     q12, q13, [sp, #480]
    ldp     q14, q15, [sp, #512]
    ldp     q16, q17, [sp, #544]
    ldp     q18, q19, [sp, #576]
    ldp     q20, q21, [sp, #608]
    ldp     q22, q23, [sp, #640]
    ldp     q24, q25, [sp, #672]
    ldp     q26, q27, [sp, #704]
    ldp     q28, q29, [sp, #736]
    ldp     q30, q31, [sp, #768]
    ldp     x0, x1, [sp, #0]
    ldp     x2, x3, [sp, #16]
    ldp     x4, x5, [sp, #32]
    ldp     x6, x7, [sp, #48]
    ldp     x8, x9, [sp, #64]
    ldp     x10, x11, [sp, #80]
    ldp     x12, x13, [sp, #96]
    ldp     x14, x15, [sp, #112]
    ldp     x16, x17, [sp, #128]
    ldp     x18, x19, [sp, #144]
    ldp     x20, x21, [sp, #160]
    ldp     x22, x23, [sp, #176]
    ldp     x24, x25, [sp, #192]
    ldp     x26, x27, [sp, #208]
    ldp     x28, x29, [sp, #224]
    ldr     x30, [sp, #240]
    add     sp, sp, #816
    eret

/* u64 proc_resume(ctx, ttbr0): continue a unit that slept in a syscall.
 * ctx = saved 288-byte frame (x0 already holds the syscall's return value). */
.global proc_resume
proc_resume:
    adr     x9, kctx
    stp     x19, x20, [x9, #0]
    stp     x21, x22, [x9, #16]
    stp     x23, x24, [x9, #32]
    stp     x25, x26, [x9, #48]
    stp     x27, x28, [x9, #64]
    stp     x29, x30, [x9, #80]
    mov     x10, sp
    str     x10, [x9, #96]
    msr     ttbr0_el1, x1
    isb
    dsb     sy
    tlbi    vmalle1is
    dsb     sy
    isb
    mov     x9, x0
    ldr     x10, [x9, #264]
    msr     sp_el0, x10
    ldp     x10, x11, [x9, #248]
    msr     elr_el1, x10
    msr     spsr_el1, x11
    ldr     x10, [x9, #800]
    ldr     x11, [x9, #808]
    msr     fpcr, x10
    msr     fpsr, x11
    ldp     q0, q1, [x9, #288]
    ldp     q2, q3, [x9, #320]
    ldp     q4, q5, [x9, #352]
    ldp     q6, q7, [x9, #384]
    ldp     q8, q9, [x9, #416]
    ldp     q10, q11, [x9, #448]
    ldp     q12, q13, [x9, #480]
    ldp     q14, q15, [x9, #512]
    ldp     q16, q17, [x9, #544]
    ldp     q18, q19, [x9, #576]
    ldp     q20, q21, [x9, #608]
    ldp     q22, q23, [x9, #640]
    ldp     q24, q25, [x9, #672]
    ldp     q26, q27, [x9, #704]
    ldp     q28, q29, [x9, #736]
    ldp     q30, q31, [x9, #768]
    ldp     x2, x3, [x9, #16]
    ldp     x4, x5, [x9, #32]
    ldp     x6, x7, [x9, #48]
    ldp     x10, x11, [x9, #80]
    ldp     x12, x13, [x9, #96]
    ldp     x14, x15, [x9, #112]
    ldp     x16, x17, [x9, #128]
    ldp     x18, x19, [x9, #144]
    ldp     x20, x21, [x9, #160]
    ldp     x22, x23, [x9, #176]
    ldp     x24, x25, [x9, #192]
    ldp     x26, x27, [x9, #208]
    ldp     x28, x29, [x9, #224]
    ldr     x30, [x9, #240]
    ldp     x0, x1, [x9, #0]
    ldr     x8, [x9, #64]
    ldr     x9, [x9, #72]
    eret

el0_irq_entry:
    sub     sp, sp, #816
    stp     x0, x1, [sp, #0]
    stp     x2, x3, [sp, #16]
    stp     x4, x5, [sp, #32]
    stp     x6, x7, [sp, #48]
    stp     x8, x9, [sp, #64]
    stp     x10, x11, [sp, #80]
    stp     x12, x13, [sp, #96]
    stp     x14, x15, [sp, #112]
    stp     x16, x17, [sp, #128]
    stp     x18, x19, [sp, #144]
    stp     x20, x21, [sp, #160]
    stp     x22, x23, [sp, #176]
    stp     x24, x25, [sp, #192]
    stp     x26, x27, [sp, #208]
    stp     x28, x29, [sp, #224]
    str     x30, [sp, #240]
    mrs     x9, elr_el1
    mrs     x10, spsr_el1
    stp     x9, x10, [sp, #248]
    mrs     x9, sp_el0
    str     x9, [sp, #264]
    stp     q0, q1, [sp, #288]
    stp     q2, q3, [sp, #320]
    stp     q4, q5, [sp, #352]
    stp     q6, q7, [sp, #384]
    stp     q8, q9, [sp, #416]
    stp     q10, q11, [sp, #448]
    stp     q12, q13, [sp, #480]
    stp     q14, q15, [sp, #512]
    stp     q16, q17, [sp, #544]
    stp     q18, q19, [sp, #576]
    stp     q20, q21, [sp, #608]
    stp     q22, q23, [sp, #640]
    stp     q24, q25, [sp, #672]
    stp     q26, q27, [sp, #704]
    stp     q28, q29, [sp, #736]
    stp     q30, q31, [sp, #768]
    mrs     x9, fpcr
    mrs     x10, fpsr
    str     x9, [sp, #800]
    str     x10, [sp, #808]
    mov     x0, sp
    bl      el0_irq                     /* C: ack, re-arm, preempt (proc_exit) — or return to continue */
    ldp     x9, x10, [sp, #248]
    msr     elr_el1, x9
    msr     spsr_el1, x10
    ldr     x9, [sp, #800]
    ldr     x10, [sp, #808]
    msr     fpcr, x9
    msr     fpsr, x10
    ldp     q0, q1, [sp, #288]
    ldp     q2, q3, [sp, #320]
    ldp     q4, q5, [sp, #352]
    ldp     q6, q7, [sp, #384]
    ldp     q8, q9, [sp, #416]
    ldp     q10, q11, [sp, #448]
    ldp     q12, q13, [sp, #480]
    ldp     q14, q15, [sp, #512]
    ldp     q16, q17, [sp, #544]
    ldp     q18, q19, [sp, #576]
    ldp     q20, q21, [sp, #608]
    ldp     q22, q23, [sp, #640]
    ldp     q24, q25, [sp, #672]
    ldp     q26, q27, [sp, #704]
    ldp     q28, q29, [sp, #736]
    ldp     q30, q31, [sp, #768]
    ldp     x0, x1, [sp, #0]
    ldp     x2, x3, [sp, #16]
    ldp     x4, x5, [sp, #32]
    ldp     x6, x7, [sp, #48]
    ldp     x8, x9, [sp, #64]
    ldp     x10, x11, [sp, #80]
    ldp     x12, x13, [sp, #96]
    ldp     x14, x15, [sp, #112]
    ldp     x16, x17, [sp, #128]
    ldp     x18, x19, [sp, #144]
    ldp     x20, x21, [sp, #160]
    ldp     x22, x23, [sp, #176]
    ldp     x24, x25, [sp, #192]
    ldp     x26, x27, [sp, #208]
    ldp     x28, x29, [sp, #224]
    ldr     x30, [sp, #240]
    add     sp, sp, #816
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
    isb
    dsb     sy
    tlbi    vmalle1is
    dsb     sy
    isb
    msr     sp_el0, x1
    msr     elr_el1, x0
    mov     x9, #0x340                  /* SPSR: EL0t, IRQ unmasked (timer slices), D A F masked */
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

/* ── Persona crt (runs at EL0, copied to the unit's page 0) ──
 * Page layout: code here; the kernel fills a data block at +0x800:
 *   +0x800 persona_main VA   +0x808 api table VA   +0x810 tick ms
 *   +0x820 api_t table (pointers to the stubs below, svc slot = 0)
 * Loop: persona_main(api, tick++); sleep(tick_ms) — the sleep syscall hands
 * the CPU back to the kernel, which resumes the unit when the tick is due. */
.global ucrt_start
.global ucrt_stubs
.global ucrt_end
ucrt_start:
    adr     x19, ucrt_start
    ldr     x20, [x19, #0x800]
    ldr     x21, [x19, #0x808]
    mov     x22, #0
1:  mov     x0, x21
    mov     x1, x22
    blr     x20
    add     x22, x22, #1
    ldr     x0, [x19, #0x810]
    mov     x8, #3                      /* SYS_SLEEP */
    svc     #0
    b       1b
    .align 4
.global rcrt_start
rcrt_start:                             /* resident variant: same page layout, ops instead of ticks */
    adr     x19, ucrt_start             /* data block is relative to the PAGE (blob start), not to us */
    ldr     x20, [x19, #0x800]          /* resident_main */
    ldr     x21, [x19, #0x808]          /* api */
    mov     x0, x21
    mov     x1, #1                      /* RES_INIT */
    mov     x2, #0
    blr     x20
    cbz     x0, 2f
    mov     x8, #1                      /* SYS_EXIT(1): init failed */
    mov     x0, #1
    svc     #0
2:  mov     x0, x21
    mov     x1, #2                      /* RES_TICK */
    mov     x2, #0
    blr     x20
    ldr     x0, [x19, #0x810]           /* tick ms */
    mov     x8, #3                      /* SYS_SLEEP → returns 1 when the kernel wants us gone */
    svc     #0
    cbz     x0, 2b
    mov     x0, x21
    mov     x1, #3                      /* RES_STOP */
    mov     x2, #0
    blr     x20
    mov     x8, #1
    mov     x0, #0
    svc     #0
    b       2b
    .align 4
ucrt_stubs:                             /* 16 bytes each, api_t order: 10 clear .. 23 touch_trace */
.macro STUB n
    mov     x8, #\n
    svc     #0
    ret
    nop
.endm
    STUB 10
    STUB 11
    STUB 12
    STUB 13
    STUB 14
    STUB 15
    STUB 16
    STUB 17
    STUB 18
    STUB 19
    STUB 20
    STUB 21
    STUB 22
    STUB 23
ucrt_end:
