/* Start code for a motion skill running as an EL0 unit on POKE.
 *
 * The kernel maps the skill's motion_io_t page at UNIT_IO_VA. This loop
 * parks the unit (SYS_WAIT) and, every time the kernel resumes it for a
 * control tick, calls the generated skill_step() once. The skill only ever
 * writes numbers into its own page; the kernel filters them for safety. */
#include "motion_io.h"

#define UNIT_IO_VA 0x100000180000UL
#define SYS_WAIT 6

static void wait_for_tick(void) {
    register long x8 __asm__("x8") = SYS_WAIT;
    register long x0 __asm__("x0") = 0;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8) : "memory");
}

__attribute__((section(".text.main"), noreturn))
void _start(void) {
    motion_io_t *io = (motion_io_t *)UNIT_IO_VA;
    for (;;) {
        wait_for_tick();
        skill_step(io);
    }
}
