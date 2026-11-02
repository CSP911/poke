/* POKE sentence runtime — what an op may call.
 *
 * An op is `void op_main(void)`. It talks to hardware only through the driver
 * fragments the hub put in the image (frags/ headers), reports with the calls
 * below, and returns. The runtime then prints the result line and powers the
 * node off. Output is ASCII on the UART; the hub reads it.
 *
 *   say("text")                  a line of narration (ASCII)
 *   report_int("key", value)     a result field, integer
 *   report_fixed("key", v, 3)    a result field, v / 10^3 with 3 decimals
 *   report_str("key", "text")    a result field, text
 *   fail("why")                  mark the request failed (op should then return)
 *   ms()                         milliseconds since this boot
 *   delay_ms(n)                  busy wait (n ≤ 10000 per call)
 */
#ifndef POKE_SENTENCE_RT_H
#define POKE_SENTENCE_RT_H
void say(const char *s);
void report_int(const char *key, long v);
void report_fixed(const char *key, long v, int decimals);
void report_str(const char *key, const char *s);
void fail(const char *why);
unsigned long ms(void);
void delay_ms(unsigned long n);
void op_main(void);
#endif
