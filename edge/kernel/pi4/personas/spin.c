/* Runaway test persona: behaves for 2 ticks, then never returns. */
#include "../poke_api.h"
static volatile unsigned long spins;
__attribute__((section(".text.main")))
unsigned long persona_main(const api_t *api, unsigned long tick) {
    if (tick == 0) { api->clear(0x00102000); api->text(40, 200, 4, 0x00FFFF00, "runaway test persona"); }
    if (tick == 2) { api->text(40, 280, 3, 0x00FF8080, "tick 2: infinite loop, never sleeping"); for (;;) spins++; }
    return 0;
}
