/* POKE sentence runtime — power, watchdog and per-target settings. See rt_power.h.
 * Built as poke_rt_power.o (runtime pages, EL1 only). Owner: 실기기팀. */
#include "rt_power.h"
typedef unsigned int u32; typedef unsigned long u64;

#define R(a) (*(volatile u32 *)(a))
#define UART  0xFE201000UL
#define PM    0xFE100000UL
#define PM_RSTC (PM + 0x1c)
#define PM_RSTS (PM + 0x20)
#define PM_WDOG (PM + 0x24)
#define PM_PASS 0x5a000000u
#define PM_RSTS_PARTITION 0x555u                    /* partition number, bits 0,2,4,6,8,10 */
#define WDOG_TICKS (15u << 16)                      /* 15 s at 65536 Hz (field max 0xFFFFF ≈ 16 s) */

/* RCFG — patched by the hub (sentence/rtcfg.py). Defaults are the twin's. */
static const volatile struct {
    u32 magic, version, wdog, budget_ms, cdiv, quiet_ms, halt, reserved;
} rtcfg __attribute__((section(".rodata.rtcfg"), used, aligned(16))) =
    { 0x47464352u /* "RCFG" */, 1, 0, 20000, 1500, 14000, 0, 0 };

static u32 wdog_armed;                              /* the quiet limit is on (wdog 1, or 2) */
static u32 wdog_pm;                                 /* the PM watchdog itself runs (wdog 1 only) */

static void wdog_feed(void) {
    R(PM_WDOG) = PM_PASS | WDOG_TICKS;
    R(PM_RSTC) = PM_PASS | (R(PM_RSTC) & ~0x30u) | 0x20;      /* WRCFG = full reset */
}

void rtp_boot(void) {
    wdog_pm = rtcfg.wdog == 1;
    wdog_armed = wdog_pm || rtcfg.wdog == 2;
    if (wdog_pm) wdog_feed();
}

void rtp_kick(void) { if (wdog_pm) wdog_feed(); }

unsigned long rtp_budget_ms(void) {
    u32 b = rtcfg.budget_ms;
    return b ? b : 20000;
}

unsigned int rtp_cdiv(void) {
    u32 c = rtcfg.cdiv;
    return c >= 2 && c <= 0xFFFE ? c & ~1u : 1500;            /* BSC CDIV: even, rounded down by hardware */
}

static u64 quiet_ticks(u64 frq) {
    u32 q = rtcfg.quiet_ms;
    if (!q || q > 15000) q = 14000;                            /* must stay under the watchdog's 15 s */
    return (u64)q * frq / 1000;
}

unsigned long rtp_compare(unsigned long now, unsigned long frq, unsigned long deadline) {
    if (!wdog_armed) return deadline;
    u64 q = now + quiet_ticks(frq);
    return q < deadline ? q : deadline;
}

const char *rtp_timeout_reason(unsigned long now, unsigned long deadline) {
    if (now >= deadline) return 0;
    return "op went too long without a runtime call (a real Pi's watchdog limit, ~14 s)";
}

/* the op's pages and the runtime's bss up to (not into) the runtime stack — what this boot held */
extern char __rt_text_end[], __rt_stack_top[];
#define RT_STACK_SIZE 0x4000UL                      /* image.ld: . = . + 0x4000; __rt_stack_top */

__attribute__((noreturn)) void rtp_power_off(void) {
    /* 1. the last line out: the reset below would cut whatever is still in the UART FIFO */
    for (u32 t = 4000000; (R(UART + 0x18) & (1u << 3)) && --t; ) {}          /* FR.BUSY */
    u32 halt = rtcfg.halt == 1;
    /* 2. nothing of this boot left in RAM: zero the op's code, data and stack and the runtime's
     *    results and page tables. The op's code pages are read-only even to EL1, so the MMU goes
     *    off first (identity map: this code keeps running at the same addresses). Data caches are
     *    off in v1, so the zeros reach DRAM. */
    u64 sc;
    __asm__ volatile("mrs %0, sctlr_el1" : "=r"(sc));
    __asm__ volatile("msr sctlr_el1, %0; isb" :: "r"(sc & ~1UL));
    for (volatile u64 *p = (volatile u64 *)__rt_text_end, *e = (volatile u64 *)((u64)__rt_stack_top - RT_STACK_SIZE); p < e; p++) *p = 0;
    __asm__ volatile("dsb sy");
    /* 3. the partition the bootloader reads after the reset: 0 = boot again (netboot), 63 = halt */
    u32 rsts = R(PM_RSTS) & ~PM_RSTS_PARTITION;
    if (halt) rsts |= PM_RSTS_PARTITION;
    R(PM_RSTS) = PM_PASS | rsts;
    /* 4. reset (~150 us) */
    R(PM_WDOG) = PM_PASS | 10;
    R(PM_RSTC) = PM_PASS | (R(PM_RSTC) & ~0x30u) | 0x20;
    for (;;) __asm__ volatile("wfe");
}
