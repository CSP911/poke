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

/* Services a resident driver can provide. Slots are ABI: append only. */
typedef struct {
    int (*touch)(int *x, int *y);         /* same contract as api->touch */
    void *reserved[7];
} poke_svc_t;

typedef struct {
    void (*clear)(unsigned int color);                                   /* +0x00 */
    void (*rect)(int x, int y, int w, int h, unsigned int color);        /* +0x08 */
    void (*text)(int x, int y, int scale, unsigned int color, const char *s); /* +0x10 */
    unsigned long (*ms)(void);            /* +0x18 ms since boot */
    unsigned long (*clock)(void);         /* +0x20 epoch seconds, 0 = unset */
    void (*gpio_out)(unsigned char pin, unsigned char val);              /* +0x28 */
    unsigned char (*gpio_in)(unsigned char pin);                         /* +0x30 */
    unsigned int (*temp_mc)(void);        /* +0x38 SoC temp in milli-celsius */
    unsigned long (*param)(int idx);      /* +0x40 hub-set runtime parameter (idx 0-7,
                                             0 when unset — personas must default) */
    int (*touch)(int *x, int *y);         /* +0x48 0 = not pressed, 1 = held,
                                             2 = new tap (returned once per press);
                                             fills panel pixel coords */
    void (*emit)(unsigned int code, unsigned long value);
                                          /* +0x50 fire an autonomous event to the
                                             hub (rate-limited to 1 per 10s per
                                             code) — call when a condition the
                                             user asked about happens */
    poke_svc_t *svc;                      /* +0x58 service table — resident drivers
                                             register here; personas never touch it */
    void (*log)(const char *s);           /* +0x60 UART log line (residents/diagnostics) */
    unsigned int (*screen)(void);         /* +0x68 (width << 16) | height, 0 = no display */
} api_t;

#define PERSONA_TICK_MS 50   /* persona_main is called every 50ms */

/* ── Resident drivers ──
 * A resident is an injected binary that keeps state and is ticked every main
 * loop iteration. It provides services by filling api->svc.
 *   u64 resident_main(const api_t *api, u64 op, u64 arg)
 *   RES_NAME → returns const char* (name), RES_INIT → 0 on success,
 *   RES_TICK → poll, RES_STOP → quiesce hardware (services are cleared by the kernel).
 * Sources live in edge/library/<arch>/<device>/, linked with resident.ld
 * (unlike personas, .data/.bss are kept). */
#define RES_INIT 1
#define RES_TICK 2
#define RES_STOP 3
#define RES_NAME 4

#endif
