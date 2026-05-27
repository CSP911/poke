// ============================================
// POKE OS ARM64 — Entry Point
// ============================================

.section .text.boot
.global _start

_start:
    // Only run on CPU 0, halt others
    mrs     x0, mpidr_el1
    and     x0, x0, #3
    cbnz    x0, halt

    // Setup stack at 48MB
    ldr     x0, =0x43000000
    mov     sp, x0

    // Zero BSS
    ldr     x0, =__bss_start
    ldr     x1, =__bss_end
1:  cmp     x0, x1
    b.ge    2f
    str     xzr, [x0], #8
    b       1b
2:

    // Jump to C
    bl      kernel_main

halt:
    wfe
    b       halt
