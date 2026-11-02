/* X4: unaligned pointers for strings and for an I2C buffer are fine (the runtime copies bytes).
 * expect: status ok, s == "bcdef", k == "xy", t2 == the temperature register's MSB (bench 23.75 °C → 23) */
#include "rt.h"
#include "tmp105.h"
static char text[16] = "abcdefXxyZ";
void op_main(void) {
    i2c_u8 raw[8];
    long t; tmp105_read_mC(0x48, &t);                       /* sets 12 bits, as a normal op would */
    text[6] = 0; text[9] = 0;
    report_str(text + 7, text + 1);                          /* key "xy", value "bcdef": both odd addresses */
    if (i2c_read(0x48, 0, raw + 1, 2)) { fail("read"); return; }
    report_int("t2", (signed char)raw[1]);
}
