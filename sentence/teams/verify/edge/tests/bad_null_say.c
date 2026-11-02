/* a typical generated-code bug: a NULL string handed to the runtime.
 * expect: POKE-FAULT kind=4, error "op handed the runtime a pointer outside its pages" */
#include "rt.h"
void op_main(void) { const char *volatile s = 0; say(s); }
