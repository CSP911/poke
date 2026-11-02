/* a typical generated-code bug: reading through a NULL pointer.
 * expect: POKE-FAULT kind=8 (esr EC 0x24), error "op touched memory it was not given", far=0x0 */
#include "rt.h"
void op_main(void) { volatile long *volatile p = 0; report_int("x", *p); }
