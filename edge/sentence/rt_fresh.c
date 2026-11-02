/* POKE sentence runtime — freshness check. See rt_fresh.h.
 * Built as poke_rt_fresh.o (runtime pages, EL1 only). Runs before the fence and the op: caches are
 * off, so everything here is small and straight — SHA-256, and RSA-2048 with e = 65537 by
 * Montgomery multiplication (17 products of 64 × 32-bit limbs). */
#include "rt_fresh.h"
#include "rt_power.h"
typedef unsigned int u32; typedef unsigned long u64; typedef unsigned char u8;

#define R(a) (*(volatile u32 *)(a))
#define UART 0xFE201000UL
#define RNG  0xFE104000UL                           /* RNG200 (BCM2711) */
#define NL   64                                     /* 2048-bit numbers, 32-bit limbs, least significant first */

/* TKEY — stamped by the hub (sentence/seal.py stamp) before it signs boot.img. e = 0: no key, no check. */
static const volatile struct {
    u32 magic, version, len, e, n0inv, wait_ms, rng, pad;     /* rng: 0 RNG200 (a Pi), 1 timing jitter (the twin) */
    u32 n[NL], rr[NL];                              /* modulus; R² mod n (R = 2^2048) */
} tkey __attribute__((section(".rodata.tkey"), used, aligned(16))) =
    { 0x59454b54u /* "TKEY" */, 1, 0, 0, 0, 0, 0, 0, {0}, {0} };
_Static_assert(sizeof tkey == 32 + 2 * 4 * NL, "TKEY layout");

extern char __rt_text_start[];                      /* where the image was loaded (image.ld) */

static void putc_(char c) { u32 t = 1000000; while ((R(UART + 0x18) & (1u << 5)) && --t) {} R(UART) = (u32)c; }
static void puts_(const char *s) { while (*s) { if (*s == '\n') putc_('\r'); putc_(*s++); } }
static void hex_(const u8 *b, u32 n) { for (u32 i = 0; i < n; i++) { putc_("0123456789abcdef"[b[i] >> 4]); putc_("0123456789abcdef"[b[i] & 15]); } }
static u64 cnt(void) { u64 v; __asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(v)); return v; }

/* ── SHA-256 ── */
static const u32 K[64] = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,
    0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,
    0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,
    0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
struct sha { u32 h[8]; u8 b[64]; u64 n; };
#define ROR(x, k) ((x) >> (k) | (x) << (32 - (k)))
static void sha_block(struct sha *s) {
    u32 w[64], a[8];
    for (int i = 0; i < 16; i++) w[i] = (u32)s->b[4*i] << 24 | (u32)s->b[4*i+1] << 16 | (u32)s->b[4*i+2] << 8 | s->b[4*i+3];
    for (int i = 16; i < 64; i++) {
        u32 s0 = ROR(w[i-15], 7) ^ ROR(w[i-15], 18) ^ (w[i-15] >> 3), s1 = ROR(w[i-2], 17) ^ ROR(w[i-2], 19) ^ (w[i-2] >> 10);
        w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    for (int i = 0; i < 8; i++) a[i] = s->h[i];
    for (int i = 0; i < 64; i++) {
        u32 t1 = a[7] + (ROR(a[4], 6) ^ ROR(a[4], 11) ^ ROR(a[4], 25)) + ((a[4] & a[5]) ^ (~a[4] & a[6])) + K[i] + w[i];
        u32 t2 = (ROR(a[0], 2) ^ ROR(a[0], 13) ^ ROR(a[0], 22)) + ((a[0] & a[1]) ^ (a[0] & a[2]) ^ (a[1] & a[2]));
        for (int j = 7; j > 0; j--) a[j] = a[j-1];
        a[4] += t1; a[0] = t1 + t2;
    }
    for (int i = 0; i < 8; i++) s->h[i] += a[i];
}
static void sha_init(struct sha *s) {
    static const u32 h0[8] = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    for (int i = 0; i < 8; i++) s->h[i] = h0[i];
    s->n = 0;
}
static void sha_add(struct sha *s, const volatile u8 *p, u64 n) {
    for (u64 i = 0; i < n; i++) { s->b[s->n++ & 63] = p[i]; if (!(s->n & 63)) sha_block(s); }
}
static void sha_end(struct sha *s, u8 out[32]) {
    u64 bits = s->n * 8; u8 one = 0x80, z = 0;
    sha_add(s, &one, 1);
    while ((s->n & 63) != 56) sha_add(s, &z, 1);
    for (int i = 7; i >= 0; i--) { u8 c = (u8)(bits >> (8 * i)); sha_add(s, &c, 1); }
    for (int i = 0; i < 32; i++) out[i] = (u8)(s->h[i >> 2] >> (24 - 8 * (i & 3)));
}

/* ── RSA-2048 verify, e = 65537 ── */
static void mont(u32 *r, const u32 *a, const u32 *b, const volatile u32 *n, u32 n0) {   /* r = a·b·R⁻¹ mod n */
    u32 t[NL + 2];
    for (int i = 0; i < NL + 2; i++) t[i] = 0;
    for (int i = 0; i < NL; i++) {
        u64 c = 0;
        for (int j = 0; j < NL; j++) { c += (u64)a[j] * b[i] + t[j]; t[j] = (u32)c; c >>= 32; }
        c += t[NL]; t[NL] = (u32)c; t[NL + 1] = (u32)(c >> 32);
        u32 m = t[0] * n0;
        c = ((u64)m * n[0] + t[0]) >> 32;
        for (int j = 1; j < NL; j++) { c += (u64)m * n[j] + t[j]; t[j - 1] = (u32)c; c >>= 32; }
        c += t[NL]; t[NL - 1] = (u32)c; t[NL] = t[NL + 1] + (u32)(c >> 32);
    }
    int ge = t[NL] != 0;
    if (!ge) { ge = 1; for (int j = NL - 1; j >= 0; j--) if (t[j] != n[j]) { ge = t[j] > n[j]; break; } }
    if (ge) { u64 bw = 0; for (int j = 0; j < NL; j++) { u64 d = (u64)t[j] - n[j] - bw; t[j] = (u32)d; bw = (d >> 32) & 1; } }
    for (int j = 0; j < NL; j++) r[j] = t[j];
}
/* sig (256 bytes, big-endian) verifies as PKCS#1 v1.5 SHA-256 over digest h, by the stamped key */
static int rsa_ok(const u8 *sig, const u8 h[32]) {
    static const u8 info[19] = {0x30,0x31,0x30,0x0d,0x06,0x09,0x60,0x86,0x48,0x01,0x65,0x03,0x04,0x02,0x01,0x05,0x00,0x04,0x20};
    u32 s[NL], x[NL], y[NL], one[NL];
    for (int j = 0; j < NL; j++) {
        s[j] = (u32)sig[255 - 4*j] | (u32)sig[254 - 4*j] << 8 | (u32)sig[253 - 4*j] << 16 | (u32)sig[252 - 4*j] << 24;
        one[j] = j == 0;
    }
    for (int j = NL - 1; j >= 0; j--) { if (s[j] < tkey.n[j]) break; if (s[j] > tkey.n[j] || j == 0) return 0; }   /* s < n */
    u32 rr[NL]; for (int j = 0; j < NL; j++) rr[j] = tkey.rr[j];
    mont(x, s, rr, tkey.n, tkey.n0inv);               /* s·R */
    for (int j = 0; j < NL; j++) y[j] = x[j];
    for (int k = 0; k < 16; k++) mont(y, y, y, tkey.n, tkey.n0inv);   /* s^65536·R */
    mont(y, y, x, tkey.n, tkey.n0inv);                /* s^65537·R */
    mont(y, y, one, tkey.n, tkey.n0inv);              /* s^65537 mod n */
    u8 m[256];
    for (int j = 0; j < NL; j++) { u32 v = y[j]; m[255 - 4*j] = (u8)v; m[254 - 4*j] = (u8)(v >> 8); m[253 - 4*j] = (u8)(v >> 16); m[252 - 4*j] = (u8)(v >> 24); }
    u32 bad = m[0] | (m[1] ^ 1u);
    for (int i = 2; i < 256 - 52; i++) bad |= m[i] ^ 0xffu;
    bad |= m[256 - 52];
    for (int i = 0; i < 19; i++) bad |= m[256 - 51 + i] ^ info[i];
    for (int i = 0; i < 32; i++) bad |= m[256 - 32 + i] ^ h[i];
    return bad == 0;
}

/* ── the nonce's randomness ── */
/* The twin: QEMU's raspi4b has no RNG200 (reading it aborts), so the twin's images take their nonce
 * from how long uneven spins take on the host — enough that no two boots repeat, NOT a security-grade
 * source. The hub stamps rng = 1 only for twin images; a Pi image always reads RNG200. */
static void jitter(u8 out[16]) {
    struct sha s; u8 h[32]; u64 v = cnt();
    sha_init(&s);
    for (int i = 0; i < 512; i++) {
        for (volatile u32 k = (u32)(v & 0xff) + 16; k; k--) {}
        v = cnt(); sha_add(&s, (const volatile u8 *)&v, 8);
    }
    sha_end(&s, h);
    for (int i = 0; i < 16; i++) out[i] = h[i];
}
static int rng_words(u32 *w, int n) {
    if (!(R(RNG + 0x00) & 0x1fffu)) {               /* not running yet: the bcm2711 bring-up Linux uses */
        R(RNG + 0x10) = 0x40000;                    /* total bit count threshold */
        R(RNG + 0x24) = 2u << 8;                    /* FIFO threshold */
        R(RNG + 0x00) = R(RNG + 0x00) | 1u;         /* RBGEN */
    }
    for (int i = 0; i < n; i++) {
        u32 t = 2000000;
        while (!(R(RNG + 0x24) & 0xffu) && --t) {}
        if (!t) return 0;
        w[i] = R(RNG + 0x20);
    }
    u32 same = 1;
    for (int i = 1; i < n; i++) same &= w[i] == w[0];
    return !same;                                   /* four equal words: not randomness */
}

static int unhex(char c) { return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1; }
static int starts(const char *l, const char *p) { while (*p) if (*l++ != *p++) return 0; return 1; }

const char *rtf_check(void) {
    if (tkey.magic != 0x59454b54u || tkey.version != 1 || tkey.e == 0) return 0;          /* lab image: no key */
    if (tkey.e != 65537) return "image carries a ticket key this runtime cannot use";
    u8 img[32], nonce[16], h[32];
    struct sha s;
    sha_init(&s); sha_add(&s, (const volatile u8 *)__rt_text_start, tkey.len); sha_end(&s, img);
    if (tkey.rng == 1) jitter(nonce);
    else {
        u32 w[4];
        if (!rng_words(w, 4)) return "no hardware randomness for a nonce";
        for (int i = 0; i < 16; i++) nonce[i] = (u8)(w[i >> 2] >> (8 * (i & 3)));
    }
    while (!(R(UART + 0x18) & (1u << 4))) (void)R(UART);           /* drop anything that came before the question */
    puts_("POKE-NONCE "); hex_(nonce, 16); putc_(' '); hex_(img, 32); puts_("\n");

    static char line[600]; int ln = 0;
    u64 frq; __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(frq)); if (!frq) frq = 54000000;
    u64 end = cnt() + (u64)(tkey.wait_ms ? tkey.wait_ms : 3000) * frq / 1000;
    while (cnt() < end) {
        rtp_kick();
        if (R(UART + 0x18) & (1u << 4)) continue;                 /* RXFE */
        char c = (char)(R(UART) & 0xff);
        if (c != '\n' && c != '\r') { if (ln < (int)sizeof line - 1) line[ln++] = c; continue; }
        line[ln] = 0;
        if (starts(line, "POKE-NOTICKET")) return "the hub gave no ticket for this boot (not the image it means to run now)";
        if (starts(line, "POKE-TICKET ")) {
            u8 sig[256]; const char *p = line + 12;
            for (int i = 0; i < 256; i++) {
                int a = unhex(p[2*i]), b = a < 0 ? -1 : unhex(p[2*i + 1]);
                if (b < 0) return "the hub's ticket is not a signature";
            }
            for (int i = 0; i < 256; i++) sig[i] = (u8)(unhex(p[2*i]) << 4 | unhex(p[2*i + 1]));
            sha_init(&s); sha_add(&s, (const volatile u8 *)"POKE-TICKET1", 12); sha_add(&s, nonce, 16); sha_add(&s, img, 32); sha_end(&s, h);
            if (!rsa_ok(sig, h)) return "ticket is not the hub's signature on this boot's nonce";
            puts_("POKE-FRESH ticket ok\n");
            return 0;
        }
        ln = 0;                                                   /* some other line: keep listening */
    }
    return "no ticket from the hub in time";
}
