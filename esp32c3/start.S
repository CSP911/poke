/* ============================================
 * POKE OS ESP32-C3 — Entry Point (Direct Boot)
 *
 * ROM bootloader detects magic 0xaedb041d at flash offset 0,
 * maps flash to 0x42000000, jumps to flash+8 = 0x42000008.
 *
 * Memory map:
 *   irom: 0x42000000 (flash code, execute via cache)
 *   drom: 0x3C000000 (flash data, read via cache)
 *   ram:  0x3FC80000 (320KB SRAM)
 * ============================================ */

    .section .text.entry
    .type _entry, @function
    .global _entry
_entry:
    /* ROM bootloader already has interrupts disabled.
     * Avoid CSR instructions that may fault in direct boot mode. */

    /* Set up global pointer */
    .option push
    .option norelax
    la gp, __global_pointer$
    .option pop

    /* Set up stack at top of RAM (0x3FC80000 + 320KB = 0x3FCD0000) */
    li sp, 0x3FCD0000

    /* Zero BSS */
    la a0, __bss_start
    la a1, __bss_end
1:
    bge a0, a1, 2f
    sw zero, 0(a0)
    addi a0, a0, 4
    j 1b
2:

    /* Jump to kernel */
    call kernel_main

    /* Should not return */
3:
    wfi
    j 3b
