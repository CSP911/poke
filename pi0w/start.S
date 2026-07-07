/* ============================================
 * POKE OS — Raspberry Pi Zero W (BCM2835) Entry
 * ARM1176JZF-S (ARMv6, 32-bit)
 *
 * GPU loads kernel.img to 0x8000, then
 * releases CPU to start executing here.
 * ============================================ */

.section .text.boot
.global _start

_start:
    /* Setup stack at 8MB */
    ldr     sp, =0x00800000

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
