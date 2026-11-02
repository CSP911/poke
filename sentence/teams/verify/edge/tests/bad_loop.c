/* the bug v0 cannot see: a loop that never ends.
 * expect: POKE-FAULT kind=timeout, error "op ran past its time budget", powered off ~OP_BUDGET_MS after boot */
#include "rt.h"
void op_main(void) { say("counting forever"); for (volatile unsigned long i = 0; ; i++) {} }
