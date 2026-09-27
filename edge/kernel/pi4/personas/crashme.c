/* Isolation test persona: behaves for 3 ticks, then touches kernel memory. */
#include "../poke_api.h"
static int ticks;
__attribute__((section(".text.main")))
unsigned long persona_main(const api_t *api, unsigned long tick) {
    if (tick == 0) { api->clear(0x00201000); api->text(40, 200, 4, 0x00FFFF00, "crash test persona"); }
    ticks++;
    if (tick == 3) {
        api->text(40, 280, 3, 0x00FF8080, "tick 3: reading kernel memory...");
        volatile unsigned long *k = (volatile unsigned long *)0x80000;
        unsigned long v = *k;                       /* EL1-only page → permission fault */
        api->text(40, 340, 3, 0x00FF0000, "UNREACHABLE");
        return v;
    }
    return 0;
}
