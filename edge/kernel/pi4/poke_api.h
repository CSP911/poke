/* ============================================
 * POKE Persona API — shared between kernel and
 * injected persona binaries.
 *
 * The kernel passes a pointer to this table in
 * x0 and the tick counter in x1:
 *   u64 persona_main(const api_t *api, u64 tick)
 *
 * Field order is ABI: never reorder, only append.
 * ============================================ */
#ifndef POKE_API_H
#define POKE_API_H

typedef struct {
    void (*clear)(unsigned int color);                                   /* +0x00 */
    void (*rect)(int x, int y, int w, int h, unsigned int color);        /* +0x08 */
    void (*text)(int x, int y, int scale, unsigned int color, const char *s); /* +0x10 */
    unsigned long (*ms)(void);            /* +0x18 ms since boot */
    unsigned long (*clock)(void);         /* +0x20 epoch seconds, 0 = unset */
    void (*gpio_out)(unsigned char pin, unsigned char val);              /* +0x28 */
    unsigned char (*gpio_in)(unsigned char pin);                         /* +0x30 */
    unsigned int (*temp_mc)(void);        /* +0x38 SoC temp in milli-celsius */
} api_t;

#define PERSONA_TICK_MS 50   /* persona_main is called every 50ms */

#endif
