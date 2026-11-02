/* POKE sentence runtime — power, watchdog and per-target settings (실기기팀, rt_power.c).
 * Runtime side only (EL1): the op never sees these, they are not in rt.h.
 *
 * The image carries one settings block, RCFG, that the hub patches per target
 * (sentence/rtcfg.py) — the code bytes are the same on the twin and on a Pi:
 *
 *   wdog       0 twin (QEMU resets the moment the PM watchdog is armed), 1 Pi,
 *              2 twin with the Pi's quiet limit but no PM watchdog (to test it)
 *   budget_ms  the op's whole run (the GIC deadline)                        default 20000
 *   cdiv       I2C bus 1 divider = core clock / 100 kHz                     twin 1500, Pi 5000
 *   quiet_ms   with wdog on: the longest an op may go without a runtime     default 14000
 *              call. The PM watchdog's maximum is ~16 s (20-bit counter at
 *              65536 Hz) — shorter than the budget — so the timer interrupt
 *              ends a silent op first and reports it, before the watchdog
 *              would reset the node without a word.
 *   halt       1: power off for real (PM_RSTS partition 63: the bootloader
 *              halts; with POWER_OFF_ON_HALT=1 and WAKE_ON_GPIO=0 the PMIC
 *              switches off). 0: reset back into the bootloader (netboot).
 */
#ifndef POKE_RT_POWER_H
#define POKE_RT_POWER_H
void rtp_boot(void);                              /* first thing after uart_init: arm the watchdog (wdog=1) */
void rtp_kick(void);                              /* on every runtime call: feed the watchdog (wdog=1) */
unsigned long rtp_budget_ms(void);
unsigned int rtp_cdiv(void);
/* the timer compare value: the budget deadline, or sooner (quiet_ms from now) while the watchdog runs */
unsigned long rtp_compare(unsigned long now, unsigned long frq, unsigned long deadline);
/* why the timer fired: 0 the budget ran out, else the reason text (an op that went quiet) */
const char *rtp_timeout_reason(unsigned long now, unsigned long deadline);
/* POKE-OFF has been printed: drain the UART, zero the op's pages and the runtime's working
 * memory (results, page tables), set the reset partition, and reset. Never returns. */
__attribute__((noreturn)) void rtp_power_off(void);
#endif
