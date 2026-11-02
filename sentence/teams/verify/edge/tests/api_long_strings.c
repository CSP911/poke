/* X4: strings longer than the runtime keeps (say 199, key 63, value 199 chars).
 * expect: status ok; the POKE-SAY line cut at 199 chars; the key cut at 63; the value at 199; one result line */
#include "rt.h"
static char big[1001];
void op_main(void) {
    for (int i = 0; i < 1000; i++) big[i] = (char)('a' + i % 26);
    big[1000] = 0;
    say(big);
    report_str(big + 500, big);
    report_int("after", 1);
}
