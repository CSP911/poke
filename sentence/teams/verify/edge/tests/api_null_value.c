/* X4: report_str with a NULL value. expect: POKE-FAULT kind=4, error "op handed the runtime a pointer outside its pages" */
#include "rt.h"
void op_main(void) { const char *volatile v = 0; report_str("k", v); report_int("after", 1); }
