/* D9: a wait over the 10 s per-call limit. expect: POKE-FAULT kind=delay, error mentions "longer waits belong to the hub", node_ms small */
#include "rt.h"
void op_main(void) { say("waiting 15 s"); delay_ms(15000); report_int("done", 1); }
