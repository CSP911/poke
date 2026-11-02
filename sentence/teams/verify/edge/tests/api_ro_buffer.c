/* X4: an I2C read into read-only memory (a string literal). The transfer happens, then handing the
 * bytes back faults. expect: POKE-FAULT kind=4, POKE-EFFECTS still shows the read of 0x48 */
#include "rt.h"
#include "tmp105.h"
void op_main(void) { const char *ro = "constant"; int e = i2c_read(0x48, 0, (i2c_u8 *)ro, 2); report_int("e", e); }
