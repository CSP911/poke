/* expect: status=ok, result {"answer":42,"pi":3.142} — v1 must not change a normal op */
#include "rt.h"
void op_main(void) { say("hello from a sentence"); report_int("answer", 42); report_fixed("pi", 3142, 3); }
