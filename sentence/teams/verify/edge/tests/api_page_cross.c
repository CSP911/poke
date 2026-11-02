/* X4: a string that runs off the end of the op's writable pages into the guard page (no NUL before it).
 * Uses the image's own layout symbol to find the edge — booted with the check off, a robustness fixture.
 * expect: POKE-FAULT kind=4 far = __op_guard, error "op handed the runtime a pointer outside its pages" */
#include "rt.h"
extern char __op_guard[];
static volatile char fill[8192];      /* volatile: kept, so the page below the guard is writable op bss */
void op_main(void) {
    char *p = __op_guard - 8;
    fill[0] = 1;
    for (int i = 0; i < 8; i++) p[i] = 'A';
    say(p);
    report_int("after", 1);
}
