/* MANIFEST {"v": 1, "uses": [{"fn": "led_set", "targets": [4], "end": "leave"}], "max_s": 1, "summary_ko": "LED 4번을 켭니다."} */
/* V5: an op with a MANIFEST: the allow list comes from it (PCA9552 0x60, LS0..LS3). expect: status=ok, leds_on == [4] */
#include "rt.h"
#include "led.h"
void op_main(void) { if (led_set(0x60, 4, 0)) { fail("led_set failed"); return; } report_int("led", 4); }
