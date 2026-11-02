/* expect: status=ok, t = bench temperature (default 23750) — the op's windows (I2C bus 1, GPIO) work at EL0 */
#include "rt.h"
#include "tmp105.h"
void op_main(void) { long t; if (tmp105_read_mC(0x48, &t)) { fail("tmp105 did not answer"); return; } report_int("t", t); }
