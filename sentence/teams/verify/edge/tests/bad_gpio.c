/* V4: GPIO is no longer an op window (the runtime sets the I2C and UART pins). Same shape as checktest's
 * "writes the UART itself"; booted with the check off. expect: kind=8 (EC 0x24), far=0xfe200004, the report still arrives */
#include "rt.h"
void op_main(void) { *(volatile unsigned *)0xFE200004UL = 0; }
