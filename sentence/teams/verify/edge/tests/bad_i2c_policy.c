/* V5: rtc_set is denied by the hub's policy, so the allow list derived from rtc.h has the clock read-only.
 * expect: POKE-FAULT kind=i2c addr=…68 write reg=…00 n=8, error "…more bytes than its manifest allows", clock unchanged */
#include "rt.h"
#include "rtc.h"
void op_main(void) { if (rtc_set(0x68, 2030, 1, 1, 0, 0, 0)) { fail("rtc_set failed"); return; } report_int("set", 1); }
