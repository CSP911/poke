/* X2: an op that turns LED 4 on and then faults (a NULL read). The room has changed before the fault,
 * so the hub must not run a new op without asking. expect: POKE-FAULT kind=8, EFFECTS shows the LED write */
#include "rt.h"
#include "led.h"
void op_main(void) { volatile long *volatile p = 0; if (led_set(0x60, 4, 0)) { fail("led"); return; } report_int("x", *p); }
