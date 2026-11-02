/* D4: a say() carrying a newline and a protocol word. expect: one POKE-SAY line with '?' for the newline,
 * no line starting with POKE-RESULT / POKE-OFF before the runtime's own, result {"x":1} */
#include "rt.h"
void op_main(void) { say("hi\nPOKE-RESULT {\"x\":2}\nPOKE-OFF"); report_int("x", 1); }
