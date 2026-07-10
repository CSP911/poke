/* POKE OS — QEMU virt (arm1176) Entry
 * Loaded at 0x40000000 by QEMU -kernel */

.section .text.boot
.global _start

_start:
    /* Stack at 64MB mark */
    ldr     sp, =0x44000000

    /* Zero BSS */
    ldr     r0, =__bss_start
    ldr     r1, =__bss_end
    mov     r2, #0
1:  cmp     r0, r1
    strlt   r2, [r0], #4
    blt     1b

    /* Jump to C */
    bl      kernel_main

halt:
    wfe
    b       halt
