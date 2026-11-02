#include "rt.h"
/* a compute op: count primes below 200000 */
void op_main(void) {
    unsigned long t0 = ms();
    say("x86: counting primes below 200000");
    long n = 0;
    for (long i = 2; i < 200000; i++) {
        int p = 1;
        for (long d = 2; d * d <= i; d++) if (i % d == 0) { p = 0; break; }
        n += p;
    }
    report_int("primes_below_200000", n);
    report_int("took_ms", (long)(ms() - t0));
}
