/* ============================================
 * POKE hardware check — RAM + cache-ECC (Pi 4, bare metal)
 *
 * Injected by `poke check …` as an EXEC probe. The hub patches P[] in the
 * binary before each shot (magic-constant patching, as poke burst does), so
 * one proven binary sweeps the whole machine chunk by chunk while the device
 * stays reachable in between.
 *
 *   P[0] start   P[1] length (bytes, multiple of 8)   P[2] pattern
 *   P[3] mode:  0 meminfo   1 fixed pattern (+inverse pass)   2 address pattern
 *               3 read the Cortex-A72 cache ECC/parity error registers (EDAC)
 *         | 0x100 = cached: build identity page tables, turn the MMU and caches
 *                   on for the fill, push the chunk to DRAM (dc civac) before
 *                   verifying, turn everything off again before returning.
 *                   Without it every access is an uncached 8-byte bus cycle
 *                   (~40 MB/s) — true but slow.
 * Page tables live at 0x00800000 (kernel ends < 1 MB, stack starts at 16 MB).
 * Regions the kernel or DMA engines use are mapped non-cacheable so no stale
 * line can ever be written back over live data:
 *   [0,16MB) kernel+GENET DMA, [0x02200000,0x02400000) xHCI DMA,
 *   [ARM end, 1GB) GPU/framebuffer, [0xFC000000,4GB) peripherals (Device).
 * ============================================ */
typedef unsigned int u32; typedef unsigned long u64;

#define MAGIC(n) (0x7a57a000UL + (n)) * 0x100000001UL
static volatile u64 P[4] = { MAGIC(1), MAGIC(2), MAGIC(3), MAGIC(4) };

#define TBL_L1   0x00800000UL
#define TBL_GB0  0x00801000UL
#define TBL_GB3  0x00802000UL
#define SCRATCH  0x01000000UL   /* relocated probe copy (cacheable); hub tests from 0x01200000 */
#define IMAGE_SZ 8192
static volatile int relocated = 0;
#define ARM_END_DEFAULT 0x3E600000UL   /* refined by mode 0 and passed back in P[2] bits for cached runs? no: hub passes it via mode 0 result; probe uses fixed split below */

static u64 cnt(void) { u64 v; __asm__ volatile("mrs %0,cntpct_el0":"=r"(v)); return v; }
static u64 frq(void) { u64 v; __asm__ volatile("mrs %0,cntfrq_el0":"=r"(v)); return v; }
static int P_(char *b, const char *s) { int n = 0; while (*s) b[n++] = *s++; return n; }
static int D_(char *b, u64 v) { char t[24]; int n = 0; if (!v) { b[0] = '0'; return 1; } while (v) { t[n++] = '0' + v % 10; v /= 10; } for (int i = 0; i < n; i++) b[i] = t[n-1-i]; return n; }
static int H_(char *b, u64 v) { const char *h = "0123456789abcdef"; b[0] = '0'; b[1] = 'x'; int n = 2; int st = 0; for (int i = 15; i >= 0; i--) { int d = (v >> (i*4)) & 0xF; if (d || st || i == 0) { b[n++] = h[d]; st = 1; } } return n; }
static u32 rd(u64 a) { return *(volatile u32 *)a; }
static void wr(u64 a, u32 v) { *(volatile u32 *)a = v; }
static void wr64(u64 a, u64 v) { *(volatile u64 *)a = v; }

/* ── VideoCore mailbox ── */
#define MBOX 0xFE00B880UL
static u32 __attribute__((aligned(16))) mb[32];
static int mbox(void) {
    u32 bus = (u32)(u64)mb + 0xC0000000U;
    __asm__ volatile("dsb sy" ::: "memory");
    while (rd(MBOX + 0x18) & 0x80000000);
    wr(MBOX + 0x20, (bus & ~0xF) | 8);
    for (int i = 0; i < 1000000; i++) {
        if (rd(MBOX + 0x18) & 0x40000000) continue;
        u32 r = rd(MBOX + 0x00);
        if ((r & 0xF) == 8) return (mb[1] & 0x80000000) != 0;
    }
    return 0;
}

/* ── MMU / caches ── */
#define ISB()  __asm__ volatile("isb" ::: "memory")
#define DSB()  __asm__ volatile("dsb sy" ::: "memory")
#define ATTR_WB   0   /* MAIR index: Normal write-back cacheable */
#define ATTR_NC   1   /* Normal non-cacheable */
#define ATTR_DEV  2   /* Device-nGnRE */
static u64 g_sh = 3;
#define BLOCK(pa, attr) ((pa) | 0x1UL | ((u64)(attr) << 2) | (g_sh << 8) | (1UL << 10))   /* block, SH, AF */
#define TABLE(pa)       ((pa) | 0x3UL)

static int current_el(void) { u64 v; __asm__ volatile("mrs %0, CurrentEL" : "=r"(v)); return (v >> 2) & 3; }

/* clean+invalidate (or invalidate) every data/unified cache level by set/way */
static void cache_all(int clean) {
    u64 clidr; __asm__ volatile("mrs %0, clidr_el1" : "=r"(clidr));
    int loc = (clidr >> 24) & 7;
    for (int lvl = 0; lvl < loc; lvl++) {
        int ctype = (clidr >> (lvl * 3)) & 7;
        if (ctype < 2) continue;
        u64 csselr = (u64)lvl << 1; __asm__ volatile("msr csselr_el1, %0" :: "r"(csselr)); ISB();
        u64 ccsidr; __asm__ volatile("mrs %0, ccsidr_el1" : "=r"(ccsidr));
        int line = (ccsidr & 7) + 4;                       /* log2(bytes) */
        int ways = ((ccsidr >> 3) & 0x3ff) + 1, sets = ((ccsidr >> 13) & 0x7fff) + 1;
        int wshift = __builtin_clz((u32)ways - 1 ? (u32)ways - 1 : 1); if (ways == 1) wshift = 32;
        for (int w = 0; w < ways; w++)
            for (int s = 0; s < sets; s++) {
                u64 v = ((u64)lvl << 1) | ((u64)s << line) | (ways == 1 ? 0 : ((u64)w << wshift));
                if (clean) __asm__ volatile("dc cisw, %0" :: "r"(v)); else __asm__ volatile("dc isw, %0" :: "r"(v));
            }
    }
    DSB();
}

static void mmu_on(u64 arm_end) {
    /* L1: 16 × 1GB (T0SZ=30 → 16GB VA). GB0 and GB3 go through 2MB tables. */
    for (int i = 0; i < 512; i++) wr64(TBL_L1 + i*8, 0);
    for (int i = 0; i < 16; i++) wr64(TBL_L1 + i*8, BLOCK((u64)i << 30, ATTR_WB));
    wr64(TBL_L1 + 0*8, TABLE(TBL_GB0));
    wr64(TBL_L1 + 3*8, TABLE(TBL_GB3));
    for (int i = 0; i < 512; i++) {
        u64 pa = (u64)i << 21; int attr = ATTR_WB;
        if (pa < 0x01000000UL) attr = ATTR_NC;                                  /* kernel, GENET DMA, our tables */
        else if (pa >= 0x02200000UL && pa < 0x02400000UL) attr = ATTR_NC;        /* xHCI DMA */
        else if (pa >= arm_end) attr = ATTR_NC;                                  /* GPU / framebuffer */
        wr64(TBL_GB0 + i*8, BLOCK(pa, attr));
        u64 pa3 = 0xC0000000UL + ((u64)i << 21);
        wr64(TBL_GB3 + i*8, BLOCK(pa3, pa3 >= 0xFC000000UL ? ATTR_DEV : ATTR_WB));
    }
    DSB();
    u64 mair = 0xFFUL | (0x44UL << 8) | (0x04UL << 16);
    cache_all(0); __asm__ volatile("ic iallu"); DSB(); ISB();
    /* Cortex-A72: data caching only works with CPUECTLR_EL1.SMPEN set (the
     * firmware leaves it clear for an EL2 kernel8) */
    { u64 e; __asm__ volatile("mrs %0, s3_1_c15_c2_1" : "=r"(e)); e |= (1UL << 6); __asm__ volatile("msr s3_1_c15_c2_1, %0" :: "r"(e)); ISB(); }
    if (current_el() == 2) {
        u64 tcr = (1UL << 31) | (1UL << 23) | (2UL << 16) | (3UL << 12) | 30;
        __asm__ volatile("msr mair_el2, %0" :: "r"(mair));
        __asm__ volatile("msr tcr_el2, %0" :: "r"(tcr));
        __asm__ volatile("msr ttbr0_el2, %0" :: "r"((u64)TBL_L1));
        __asm__ volatile("tlbi alle2"); DSB(); ISB();
        u64 s; __asm__ volatile("mrs %0, sctlr_el2" : "=r"(s)); s |= (1UL << 0) | (1UL << 2) | (1UL << 12);
        __asm__ volatile("msr sctlr_el2, %0" :: "r"(s)); ISB();
    } else {
        u64 tcr = (2UL << 32) | (1UL << 23) | (3UL << 12) | 30;
        __asm__ volatile("msr mair_el1, %0" :: "r"(mair));
        __asm__ volatile("msr tcr_el1, %0" :: "r"(tcr));
        __asm__ volatile("msr ttbr0_el1, %0" :: "r"((u64)TBL_L1));
        __asm__ volatile("tlbi vmalle1"); DSB(); ISB();
        u64 s; __asm__ volatile("mrs %0, sctlr_el1" : "=r"(s)); s |= (1UL << 0) | (1UL << 2) | (1UL << 12);
        __asm__ volatile("msr sctlr_el1, %0" :: "r"(s)); ISB();
    }
}

static void mmu_off(void) {
    DSB(); cache_all(1);                                   /* test data is garbage, but leave nothing dirty */
    if (current_el() == 2) { u64 s; __asm__ volatile("mrs %0, sctlr_el2" : "=r"(s)); s &= ~((1UL << 0) | (1UL << 2) | (1UL << 12)); __asm__ volatile("msr sctlr_el2, %0" :: "r"(s)); ISB(); __asm__ volatile("tlbi alle2"); }
    else                   { u64 s; __asm__ volatile("mrs %0, sctlr_el1" : "=r"(s)); s &= ~((1UL << 0) | (1UL << 2) | (1UL << 12)); __asm__ volatile("msr sctlr_el1, %0" :: "r"(s)); ISB(); __asm__ volatile("tlbi vmalle1"); }
    DSB(); cache_all(0); __asm__ volatile("ic iallu"); DSB(); ISB();
}

static void push_to_dram(u64 start, u64 len) {           /* clean+invalidate by VA: verify must read DRAM */
    for (u64 a = start; a < start + len; a += 64) __asm__ volatile("dc civac, %0" :: "r"(a));
    DSB();
}

__attribute__((section(".text.main")))
u64 probe(char *out, int *outlen) {
    int n = 0;
    u64 start = P[0], len = P[1], pat = P[2], mode = P[3] & 0xff, cached = P[3] & 0x100;

    /* Cached runs execute from a copy above 16MB: the kernel region stays
     * non-cacheable (GENET DMA lives there), and code fetched from
     * non-cacheable memory would make every loop DRAM-bound. */
    if (cached && mode != 0 && !relocated) {
        relocated = 1;                                       /* the copy inherits this */
        volatile u64 *src = (volatile u64 *)(u64)probe, *dst = (volatile u64 *)SCRATCH;
        for (u64 i = 0; i < IMAGE_SZ/8; i++) dst[i] = src[i];
        __asm__ volatile("dsb sy; ic iallu; dsb sy; isb" ::: "memory");
        u64 (*copy)(char *, int *) = (u64 (*)(char *, int *))SCRATCH;
        relocated = 0;
        return copy(out, outlen);
    }

    if (mode == 0) {
        mb[0] = 8*4; mb[1] = 0; mb[2] = 0x00010002; mb[3] = 4; mb[4] = 0; mb[5] = 0; mb[6] = 0; mb[7] = 0;
        int ok = mbox(); u32 rev = mb[5];
        mb[0] = 8*4; mb[1] = 0; mb[2] = 0x00010005; mb[3] = 8; mb[4] = 0; mb[5] = 0; mb[6] = 0; mb[7] = 0;
        int ok2 = mbox(); u32 arm_base = mb[5], arm_size = mb[6];
        n += P_(out+n, "rev="); n += H_(out+n, rev);
        n += P_(out+n, " arm_end="); n += H_(out+n, ok2 ? (u64)arm_base + arm_size : 0);
        n += P_(out+n, " el="); n += D_(out+n, current_el());
        n += P_(out+n, ok ? " mbox=ok" : " mbox=fail");
        *outlen = n; return 0;
    }
    if (mode == 3) {                                        /* EDAC: A72 L1 (CPUMERRSR) + L2 (L2MERRSR) */
        u64 c, l;
        __asm__ volatile("mrs %0, s3_1_c15_c2_2" : "=r"(c));
        __asm__ volatile("mrs %0, s3_1_c15_c2_3" : "=r"(l));
        if (pat) { __asm__ volatile("msr s3_1_c15_c2_2, xzr"); __asm__ volatile("msr s3_1_c15_c2_3, xzr"); }   /* clear */
        n += P_(out+n, "cpumerrsr="); n += H_(out+n, c);
        n += P_(out+n, " l2merrsr="); n += H_(out+n, l);
        *outlen = n; return 0;
    }

    if (mode == 5) {                                        /* diag: is translation really ours? alias GB1 → GB2 */
        u64 sct0; __asm__ volatile("mrs %0, sctlr_el2" : "=r"(sct0));
        wr64(0x40000000UL, 0xAAAA000000000001UL); wr64(0x80000000UL, 0xBBBB000000000002UL);
        mmu_on(ARM_END_DEFAULT);
        wr64(TBL_L1 + 1*8, BLOCK(0x80000000UL, ATTR_WB)); DSB(); __asm__ volatile("tlbi alle2"); DSB(); ISB();
        u64 seen = *(volatile u64 *)0x40000000UL;
        u64 par1, par2, actlr, ccs; __asm__ volatile("at s1e2r, %0" :: "r"(0x40000000UL)); ISB(); __asm__ volatile("mrs %0, par_el1" : "=r"(par1));
        __asm__ volatile("at s1e2r, %0" :: "r"(0x00090000UL)); ISB(); __asm__ volatile("mrs %0, par_el1" : "=r"(par2));
        __asm__ volatile("mrs %0, s3_1_c15_c2_0" : "=r"(actlr));
        __asm__ volatile("msr csselr_el1, xzr"); ISB(); __asm__ volatile("mrs %0, ccsidr_el1" : "=r"(ccs));
        u64 sct1; __asm__ volatile("mrs %0, sctlr_el2" : "=r"(sct1));
        u64 ttbr, tcr, mair; __asm__ volatile("mrs %0, ttbr0_el2" : "=r"(ttbr)); __asm__ volatile("mrs %0, tcr_el2" : "=r"(tcr)); __asm__ volatile("mrs %0, mair_el2" : "=r"(mair));
        mmu_off();
        n += P_(out+n, "sctlr_before="); n += H_(out+n, sct0); n += P_(out+n, " after="); n += H_(out+n, sct1);
        n += P_(out+n, " va40000000_reads="); n += H_(out+n, seen); n += P_(out+n, (seen >> 48) == 0xBBBB ? " (ALIAS: translating)" : " (identity: NOT translating)");
        n += P_(out+n, " par@1GB="); n += H_(out+n, par1); n += P_(out+n, " par@kernel="); n += H_(out+n, par2);
        n += P_(out+n, " cpuactlr="); n += H_(out+n, actlr); n += P_(out+n, " ccsidr_l1d="); n += H_(out+n, ccs);
        *outlen = n; return 0;
    }
    if (mode == 4) {                                        /* diag: does the MMU really come on? */
        g_sh = (pat & 1) ? 0 : 3;
        mmu_on(ARM_END_DEFAULT);
        u64 sct; if (current_el() == 2) __asm__ volatile("mrs %0, sctlr_el2" : "=r"(sct)); else __asm__ volatile("mrs %0, sctlr_el1" : "=r"(sct));
        u64 *m = (u64 *)start; u64 words = len / 8;
        u64 t0 = cnt(); for (u64 i = 0; i < words; i += 4) { m[i] = 1; m[i+1] = 1; m[i+2] = 1; m[i+3] = 1; } __asm__ volatile("" ::: "memory");
        u64 t1 = cnt(); push_to_dram(start, len); u64 t2 = cnt();
        u64 sum = 0; for (u64 i = 0; i < words; i++) sum += ((volatile u64 *)m)[i]; u64 t3 = cnt();
        mmu_off();
        n += P_(out+n, "el="); n += D_(out+n, current_el()); n += P_(out+n, " sctlr="); n += H_(out+n, sct);
        n += P_(out+n, " fill_ms="); n += D_(out+n, (t1-t0)*1000/frq());
        n += P_(out+n, " flush_ms="); n += D_(out+n, (t2-t1)*1000/frq());
        n += P_(out+n, " read_ms="); n += D_(out+n, (t3-t2)*1000/frq());
        n += P_(out+n, " sum="); n += D_(out+n, sum);
        /* cache-hit probe: one 64B line read 1M× — cached ≈ 1-2 ms, uncached ≈ 400 ms */
        mmu_on(ARM_END_DEFAULT);
        u64 t4 = cnt(); u64 acc = 0; for (u64 r = 0; r < 1000000; r++) acc += ((volatile u64 *)m)[r & 7]; u64 t5 = cnt();
        u64 ect, hcr; __asm__ volatile("mrs %0, s3_1_c15_c2_1" : "=r"(ect)); __asm__ volatile("mrs %0, hcr_el2" : "=r"(hcr));
        mmu_off();
        n += P_(out+n, " sh="); n += D_(out+n, g_sh); n += P_(out+n, " line1M_ms="); n += D_(out+n, (t5-t4)*1000/frq()); n += P_(out+n, " acc="); n += D_(out+n, acc & 0xff);
        n += P_(out+n, " cpuectlr="); n += H_(out+n, ect); n += P_(out+n, " hcr="); n += H_(out+n, hcr);
        *outlen = n; return 0;
    }
    if (cached) mmu_on(ARM_END_DEFAULT);

    u64 *m = (u64 *)start;
    u64 words = len / 8, errs = 0, first = 0, fexp = 0, fgot = 0;
    u64 t0 = cnt();
    for (int pass = 0; pass < 2; pass++) {
        u64 inv = pass ? ~0UL : 0UL;
        if (mode == 2) { for (u64 i = 0; i < words; i++) m[i] = ((start + i*8) ^ pat) ^ inv; }
        else           { u64 v = pat ^ inv; for (u64 i = 0; i < words; i += 4) { m[i] = v; m[i+1] = v; m[i+2] = v; m[i+3] = v; } }
        __asm__ volatile("" ::: "memory");
        if (cached) push_to_dram(start, len);
        for (u64 i = 0; i < words; i++) {
            u64 exp = (mode == 2 ? ((start + i*8) ^ pat) : pat) ^ inv;
            u64 got = ((volatile u64 *)m)[i];
            if (got != exp) { if (!errs) { first = start + i*8; fexp = exp; fgot = got; } errs++; }
        }
    }
    u64 ms = (cnt() - t0) * 1000 / frq();
    if (cached) mmu_off();
    if (!errs) { n += P_(out+n, "ok w="); n += D_(out+n, words); n += P_(out+n, " ms="); n += D_(out+n, ms); }
    else {
        n += P_(out+n, "err n="); n += D_(out+n, errs);
        n += P_(out+n, " first="); n += H_(out+n, first);
        n += P_(out+n, " exp="); n += H_(out+n, fexp);
        n += P_(out+n, " got="); n += H_(out+n, fgot);
        n += P_(out+n, " ms="); n += D_(out+n, ms);
    }
    *outlen = n; return errs;
}
