/* ============================================
 * POKE sentence image v1 — one request = one boot, the op fenced in.
 *
 * Same contract as v0 (the runtime boots, runs the op once, reports, powers
 * off), one change: the op runs at EL0 under a page table the runtime builds
 * (pt.h). The op sees its own pages and its windows and nothing else; the
 * runtime is reached only through `svc` (ustub.S); a generic-timer interrupt
 * ends an op that overruns its time. Any fault → report → power off.
 *
 * Core 0 only. The firmware hands us EL2 (or EL1); the runtime runs at EL1.
 * ============================================ */
.section .text.boot
.global _start
_start:
    mrs     x0, mpidr_el1
    and     x0, x0, #3
    cbnz    x0, park
    ldr     x0, =traps
    mrs     x1, CurrentEL
    lsr     x1, x1, #2
    cmp     x1, #2
    b.ne    1f
    msr     vbar_el2, x0               /* anything that still reaches EL2 reports too */
    mov     x2, #(1 << 31)             /* HCR_EL2.RW: EL1 is AArch64; no traps, IRQs to EL1 */
    msr     hcr_el2, x2
    mov     x2, #3                     /* CNTHCTL_EL2: EL1 owns the physical counter and timer */
    msr     cnthctl_el2, x2
    msr     cntvoff_el2, xzr
    mov     x2, #0x33ff                /* CPTR_EL2: no traps to EL2 */
    msr     cptr_el2, x2
    msr     hstr_el2, xzr
    ldr     x2, =0x30d00800            /* SCTLR_EL1: RES1 only; MMU on later, in rt.c */
    msr     sctlr_el1, x2
    mov     x2, #0x3c5                 /* EL1h, D A I F masked */
    msr     spsr_el2, x2
    adr     x2, 1f
    msr     elr_el2, x2
    eret
1:  ldr     x0, =traps
    msr     vbar_el1, x0
    ldr     x0, =__rt_stack_top
    mov     sp, x0
    isb
    ldr     x0, =__op_bss_start        /* op bss, guard, op stack, runtime bss (tables), runtime stack */
    ldr     x1, =__rt_end
3:  cmp     x0, x1
    b.ge    4f
    str     xzr, [x0], #8
    b       3b
4:  bl      sentence_boot             /* never returns */
park:
    wfe
    b       park

/* enter_el0(entry, sp_el0, lr): run the op at EL0 with only the timer IRQ open.
 * The op returns through lr (op_return in ustub.S → svc #0). Never returns here. */
.global enter_el0
enter_el0:
    msr     elr_el1, x0
    msr     sp_el0, x1
    mov     x30, x2
    mov     x3, #0x340                 /* EL0t; D A F masked, I open */
    msr     spsr_el1, x3
    mov     x0, xzr                    /* the op starts with nothing of the runtime's in registers */
    mov     x1, xzr
    mov     x2, xzr
    mov     x3, xzr
    mov     x4, xzr
    mov     x5, xzr
    mov     x6, xzr
    mov     x7, xzr
    mov     x8, xzr
    mov     x9, xzr
    mov     x10, xzr
    mov     x11, xzr
    mov     x12, xzr
    mov     x13, xzr
    mov     x14, xzr
    mov     x15, xzr
    mov     x16, xzr
    mov     x17, xzr
    mov     x18, xzr
    mov     x29, xzr
    eret

/* any exception: report (esr, elr, far) and power off — except svc from the op */
.macro TRAP kind
    .balign 0x80
    mov     x0, #\kind
    b       trap_common
.endm
.balign 0x800
traps:
    TRAP 0
    TRAP 1
    TRAP 2
    TRAP 3
    TRAP 4                             /* runtime fault, or a pointer the op handed over it may not read */
    TRAP 5
    TRAP 6
    TRAP 7
    .balign 0x80                       /* 8: from the op, synchronous — svc, or a fault */
    b       el0_sync
    TRAP 9                             /* from the op, IRQ — the time budget ran out */
    TRAP 10
    TRAP 11
    TRAP 12
    TRAP 13
    TRAP 14
    TRAP 15

/* svc #n from the op: a runtime call. x0..x2 args, x0 result. The stub is a
 * normal call for the op's compiler (x0-x18 clobbered); x19-x29 survive by
 * the C ABI, x30 is kept here. ELR/SPSR stay put: nothing in a call returns
 * through another exception. */
el0_sync:
    stp     x29, x30, [sp, #-16]!
    mrs     x9, esr_el1
    lsr     x10, x9, #26
    cmp     x10, #0x15                 /* EC: SVC from AArch64 */
    b.ne    1f
    and     x3, x9, #0xffff            /* svc immediate = call number */
    bl      sentence_svc
    ldp     x29, x30, [sp], #16
    eret
1:  mov     x0, #8
    b       trap_common

trap_common:
    ldr     x1, =__rt_stack_top
    mov     sp, x1
    mrs     x1, CurrentEL
    lsr     x1, x1, #2
    cmp     x1, #2
    b.ne    5f
    mrs     x1, esr_el2
    mrs     x2, elr_el2
    mrs     x3, far_el2
    b       6f
5:  mrs     x1, esr_el1
    mrs     x2, elr_el1
    mrs     x3, far_el1
6:  bl      sentence_fault             /* never returns */
    b       park
