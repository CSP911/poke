/* Persona: stopwatch — counts up from injection moment */
#include "../poke_api.h"

#define BG 0x00181020
#define FG 0x0060A0FF
#define WH 0x00FFFFFF

static void two(char *p, unsigned v) { p[0] = '0' + v / 10; p[1] = '0' + v % 10; }

__attribute__((section(".text.main")))
unsigned long persona_main(const api_t *api, unsigned long tick) {
    if (tick == 0) {
        api->clear(BG);
        api->text(80, 40, 4, FG, "TIMER");
        api->rect(80, 90, 640, 4, FG);
        api->text(80, 400, 2, 0x00808080, "persona: timer");
    }
    if (tick % 2) return 0;               /* refresh every 100ms */

    unsigned long t100 = tick * PERSONA_TICK_MS / 100;   /* 100ms units */
    char b[8];
    two(b, (t100 / 600) % 100); b[2] = ':';
    two(b + 3, (t100 / 10) % 60); b[5] = '.';
    b[6] = '0' + t100 % 10; b[7] = 0;

    api->rect(80, 180, 660, 90, BG);
    api->text(80, 180, 10, WH, b);

    /* seconds progress dot bar */
    int w = (int)((t100 % 600) * 672 / 600);
    api->rect(64, 310, 672, 16, 0x00202030);
    api->rect(64, 310, w, 16, FG);
    return 0;
}
