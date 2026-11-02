/* POKE sentence runtime for x86 PCs (PXE) — what an op may call. Same calls as the Pi runtime
 * (edge/sentence/rt.h), so one op source serves both bodies.
 *
 * The image is a Multiboot ELF that a PC's network boot (iPXE) loads at 1 MiB. The op runs in
 * ring 3 behind a page table: its own pages and nothing else — no I/O ports, no runtime memory.
 * Each call below is `int 0x80` (ustub.S) into the runtime. A fault, a pointer the op does not
 * own, or overrunning the time budget (20 s) ends the run with POKE-FAULT and {"error": why}.
 * At the end the runtime zeroes everything the run used and resets the PC — whose next boot,
 * with no sentence pending at the hub, falls through to its own disk.
 *
 *   say("text")                  a line of narration
 *   report_int("key", value)     a result field, integer
 *   report_fixed("key", v, 3)    a result field, v / 10^3 with 3 decimals
 *   report_str("key", "text")    a result field, text
 *   fail("why")                  mark the request failed (op should then return)
 *   ms()                         milliseconds since this boot
 *   delay_ms(n)                  wait (n ≤ 10000 per call)
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
