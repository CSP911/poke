/* Persona: wall clock — HH:MM:SS from hub-injected time */
#include "../poke_api.h"

#define BG 0x00101828
#define FG 0x0000FF66
#define WH 0x00FFFFFF

static void two(char *p, unsigned v) { p[0] = '0' + v / 10; p[1] = '0' + v % 10; }

__attribute__((section(".text.main")))
unsigned long persona_main(const api_t *api, unsigned long tick) {
    if (tick == 0) {
        api->clear(BG);
        api->text(80, 40, 4, FG, "CLOCK");
        api->rect(80, 90, 640, 4, FG);
        api->text(80, 400, 2, 0x00808080, "persona: clock");
    }
    if (tick % 10) return 0;              /* refresh every 500ms */

    unsigned long sec = api->clock();
    if (!sec) { api->text(80, 190, 6, WH, "NO TIME"); return 0; }

    unsigned long d = sec % 86400;
    char b[9];
    two(b, d / 3600); b[2] = ':';
    two(b + 3, (d / 60) % 60); b[5] = ':';
    two(b + 6, d % 60); b[8] = 0;

    api->rect(80, 180, 660, 90, BG);
    api->text(80, 180, 10, WH, b);
    return 0;
}
