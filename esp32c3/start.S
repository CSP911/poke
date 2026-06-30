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
    /* Set up global pointer */
    .option push
    .option norelax
    la gp, __global_pointer$
    .option pop

    /* Set up stack at top of RAM (0x3FC80000 + 320KB = 0x3FCD0000) */
    li sp, 0x3FCD0000

    /* Copy .data from Flash (LMA) to RAM (VMA) */
    la a0, _data_start      /* destination (RAM) */
    la a1, _data_lma         /* source (Flash, mapped to drom) */
    la a2, _data_size        /* byte count */
    add a2, a0, a2           /* a2 = end address */
1:
    bge a0, a2, 2f
    lw t0, 0(a1)
    sw t0, 0(a0)
    addi a0, a0, 4
    addi a1, a1, 4
    j 1b
2:

    /* Zero BSS */
    la a0, __bss_start
    la a1, __bss_end
3:
    bge a0, a1, 4f
    sw zero, 0(a0)
    addi a0, a0, 4
    j 3b
4:

    /* Jump to kernel */
    call kernel_main

    /* Should not return */
5:
    wfi
    j 5b
