/* expect: status=ok, waited_ms ≈ 1500 — runtime calls (delay_ms, ms) cross the svc boundary */
#include "rt.h"
void op_main(void) { unsigned long a = ms(); for (int i = 0; i < 3; i++) delay_ms(500); report_int("waited_ms", (long)(ms() - a)); }
