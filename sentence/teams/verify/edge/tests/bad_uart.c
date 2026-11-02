/* checktest.py case 1 (v0 refuses it); booted with the check OFF to show v1 holds on its own.
 * expect: POKE-FAULT kind=8 (EC 0x24), far=0xfe201000, no stray 'A' on the serial line */
#include "rt.h"
void op_main(void) { *(volatile unsigned *)0xFE201000UL = 65; }
