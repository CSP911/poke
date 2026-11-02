/* POKE sentence runtime — freshness: this boot is the one the hub asked for, now (rt_fresh.c).
 * Runtime side only (EL1), like rt_power.h.
 *
 * The Pi's bootloader checks that the hub signed boot.img, not when: an image the hub signed
 * yesterday is still signed today, and anyone on the wire can send it again. So before the op runs
 * (before the I2C pins are even set up) the runtime asks the hub over its UART:
 *
 *   node → hub   POKE-NONCE <16 random bytes, hex> <sha256 of this image as loaded, hex>
 *   hub  → node  POKE-TICKET <RSA-2048 PKCS#1 v1.5 over SHA-256("POKE-TICKET1" ‖ nonce ‖ image hash), hex>
 *                or POKE-NOTICKET (the hub does not want this image now)
 *
 * The nonce comes from the SoC's hardware RNG (RNG200), so a ticket cannot be recorded and
 * replayed; the hub gives one ticket per image it means to run, shortly after serving it.
 * The hub's public key sits in the TKEY block, which the hub stamps in before it signs boot.img
 * (sentence/seal.py stamp), so the key is covered by the bootloader's own check.
 * An image without a stamped key (the lab's direct -kernel boots) skips the check.
 */
#ifndef POKE_RT_FRESH_H
#define POKE_RT_FRESH_H
/* 0: fresh (or no key stamped); else why this boot must not run its op. Prints its own lines. */
const char *rtf_check(void);
#endif
