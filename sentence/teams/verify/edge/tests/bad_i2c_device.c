/* V5: a transfer to a device the op's allow list does not have (it includes only tmp105.h, so only 0x48).
 * expect: POKE-FAULT kind=i2c addr=…60, error "…not in its manifest", LED state unchanged */
#include "rt.h"
#include "tmp105.h"
void op_main(void) { i2c_u8 b[2] = { 0x06, 0x00 }; long t; tmp105_read_mC(0x48, &t); report_int("t", t); i2c_write(0x60, b, 2); report_int("after", 1); }
