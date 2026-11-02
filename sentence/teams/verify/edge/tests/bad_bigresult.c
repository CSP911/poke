/* D2: more than 1 KB of result fields. expect: status=ok, valid JSON with "truncated":true, fields k0.. intact */
#include "rt.h"
void op_main(void) { static const char *k[] = {"k0","k1","k2","k3","k4","k5","k6","k7","k8","k9"};
    for (int r = 0; r < 20; r++) for (int i = 0; i < 10; i++) report_str(k[i], "0123456789012345678901234567890123456789"); }
