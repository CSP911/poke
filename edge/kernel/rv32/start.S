/* ============================================
 * POKE OS RV32 — Entry Point
 * Target: QEMU virt machine (riscv32)
 * DRAM base: 0x80000000
 * ============================================ */

.section .text.boot
.global _start

_start:
    /* Disable interrupts */
    csrw    mie, zero

    /* Only run on hart 0, park others */
    csrr    t0, mhartid
    bnez    t0, park

    /* Setup stack at 0x80400000 (base + 4MB) */
    li      sp, 0x80400000

    /* Zero BSS */
    la      t0, __bss_start
    la      t1, __bss_end
1:
    bge     t0, t1, 2f
    sw      zero, 0(t0)
    addi    t0, t0, 4
    j       1b
2:

    /* Jump to C kernel */
    call    kernel_main

park:
    wfi
    j       park
