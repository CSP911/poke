/* X4: negative and oversized lengths. i2c_read(…, -1) and a 65-byte write come back as 1 (bad request),
 * nothing reaches the bus; report_fixed with -5 / 99 decimals is clamped. expect: status ok,
 * r == 1, w == 1, f1 == "12", f2 has 18 decimals; POKE-EFFECTS shows no transfer with n > 64 */
#include "rt.h"
#include "tmp105.h"
void op_main(void) {
    i2c_u8 b[65];
    for (int i = 0; i < 65; i++) b[i] = 0;
    report_int("r", i2c_read(0x48, 0, b, (i2c_u32)-1));
    report_int("w", i2c_write(0x48, b, 65));
    report_int("x", (long)poke_i2c_xfer(0x48, 1, b, 0xFFFFFFFFu));
    report_fixed("f1", 12, -5);
    report_fixed("f2", 12, 99);
}
