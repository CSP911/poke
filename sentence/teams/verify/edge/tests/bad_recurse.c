/* D3: recursion that never ends. v0: the stack ran into the runtime. v1: it runs into the guard page.
 * expect: POKE-FAULT kind=8 (EC 0x24), far inside the guard page (__op_guard), error "op touched memory it was not given" */
#include "rt.h"
static long down(long n) { volatile char pad[256]; pad[0] = (char)n; return down(n + 1) + pad[0]; }
void op_main(void) { report_int("depth", down(0)); }
