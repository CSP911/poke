/* X4: delay_ms(-1) is a huge wait. expect: POKE-FAULT kind=delay at once (not a 20 s timeout) */
#include "rt.h"
void op_main(void) { long n = -1; delay_ms((unsigned long)n); report_int("after", 1); }
