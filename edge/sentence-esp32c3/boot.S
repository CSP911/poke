/* ESP32-C3 sentence image: entry from the ROM's load_ram, and the final scrub. */
        .section .text.entry, "ax"
        .globl _start
_start:
        la      sp, __stack_top
        la      t0, __bss_start                  /* zero bss (runtime + op) */
        la      t1, __bss_end
1:      bgeu    t0, t1, 2f
        sw      zero, 0(t0)
        addi    t0, t0, 4
        j       1b
2:      call    sentence_boot
3:      j       3b

/* scrub_and_reset(start, end): zero [start, end) — the op's code and data, the runtime's result and
 * this stack — with registers only, then the ROM's software reset (its address is a constant: the
 * code that holds it is being zeroed). This routine itself sits outside [start, end). */
        .section .text.keep, "ax"
        .globl scrub_and_reset
scrub_and_reset:
        mv      t0, a0
        mv      t1, a1
4:      bgeu    t0, t1, 5f
        sw      zero, 0(t0)
        addi    t0, t0, 4
        j       4b
5:      li      t2, 0x40000090                   /* ROM software_reset */
        jalr    zero, 0(t2)
6:      j       6b
