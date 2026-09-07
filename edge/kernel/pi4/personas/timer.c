/* Persona: countdown timer — param[0] = duration in seconds (0 → 60).
 * Flashes the screen when time is up. */
#include "../poke_api.h"

#define BG 0x00181020
#define FG 0x0060A0FF
#define WH 0x00FFFFFF
#define ALERT 0x00FF3030

static void two(char *p, unsigned v) { p[0] = '0' + v / 10; p[1] = '0' + v % 10; }

static void draw_title(const api_t *api) {
    api->clear(BG);
    api->text(80, 40, 4, FG, "TIMER");
    api->rect(80, 100, 640, 4, FG);
    api->text(80, 400, 2, 0x00808080, "persona: timer");
}

__attribute__((section(".text.main")))
unsigned long persona_main(const api_t *api, unsigned long tick) {
    unsigned long dur = api->param(0);
    if (!dur) dur = 60;

    if (tick == 0) draw_title(api);

    unsigned long elapsed = tick / 20;
    if (elapsed < dur) {
        if (tick % 20) return 0;              /* update once per second */
        unsigned long remaining = dur - elapsed;
        char b[6];
        two(b, (remaining / 60) % 100); b[2] = ':'; two(b + 3, remaining % 60); b[5] = 0;
        api->rect(200, 180, 400, 110, BG);
        api->text(200, 180, 10, WH, b);
        int w = (int)(elapsed * 672 / dur);
        api->rect(64, 320, 672, 16, 0x00202030);
        api->rect(64, 320, w, 16, FG);
        return 0;
    }

    /* time's up: flash */
    if (tick % 10) return 0;
    if ((tick / 10) % 2) {
        api->clear(ALERT);
        api->text(280, 200, 6, WH, "DONE!");
    } else {
        draw_title(api);
    }
    return 0;
}
