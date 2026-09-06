/* Persona: thermometer — live SoC temperature with bar graph */
#include "../poke_api.h"

#define BG 0x00101828
#define FG 0x00FFB020
#define WH 0x00FFFFFF

__attribute__((section(".text.main")))
unsigned long persona_main(const api_t *api, unsigned long tick) {
    if (tick == 0) {
        api->clear(BG);
        api->text(80, 40, 4, FG, "THERMO");
        api->rect(80, 90, 640, 4, FG);
        api->text(64, 360, 2, 0x00808080, "30C");
        api->text(672, 360, 2, 0x00808080, "80C");
        api->text(80, 400, 2, 0x00808080, "persona: thermo");
    }
    if (tick % 20) return 0;              /* refresh every 1s */

    unsigned int t = api->temp_mc();
    unsigned int deg = t / 1000, frac = (t % 1000) / 100;

    char b[7];
    b[0] = '0' + (deg / 10) % 10; b[1] = '0' + deg % 10;
    b[2] = '.'; b[3] = '0' + frac; b[4] = ' '; b[5] = 'C'; b[6] = 0;

    api->rect(80, 170, 660, 90, BG);
    api->text(80, 170, 10, FG, b);

    /* bar: 30C..80C mapped to 0..672 px */
    int w = 0;
    if (t > 30000) w = (int)((t - 30000) / 74);   /* 50000/672 ≈ 74 */
    if (w > 672) w = 672;
    api->rect(64, 310, 672, 30, 0x00202838);
    api->rect(64, 310, w, 30, (t > 60000) ? 0x00FF4040 : 0x0000FF66);
    return 0;
}
