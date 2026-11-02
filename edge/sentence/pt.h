/* POKE sentence runtime v1 — the op's page table.
 *
 * Identity map (VA == PA), 4 KB granule, 32-bit VA (T0SZ=32, walk starts at level 1).
 * Only what is listed here exists; everything else faults.
 *
 *   runtime code+rodata   EL1 read/exec            EL0: nothing
 *   runtime bss+stack     EL1 read/write, no exec  EL0: nothing   (includes these tables)
 *   op code (+ svc stubs) EL0 read/exec            EL1: no exec
 *   op rodata             EL0 read                 no exec
 *   op data/bss/stack     EL0 read/write           no exec        (a guard page below the stack)
 *   op windows            none since V5 — the op reaches I2C through the runtime (svc, allow list)
 *   runtime devices       EL1 only, Device         (GPIO, UART, BSC1, PM watchdog, GIC)
 *
 * Pure function over a layout so the hub can test it off-target (pt_test.c). */
#ifndef POKE_PT_H
#define POKE_PT_H
typedef unsigned long pt_u64;

#define PT_TBL    3UL
#define PT_PAGE   3UL
#define PT_BLOCK  1UL
#define PT_AF     (1UL << 10)
#define PT_ISH    (3UL << 8)
#define PT_EL1RW  (0UL << 6)
#define PT_RW     (1UL << 6)            /* EL1 + EL0 read/write */
#define PT_EL1RO  (2UL << 6)
#define PT_RO     (3UL << 6)            /* EL1 + EL0 read-only */
#define PT_PXN    (1UL << 53)
#define PT_UXN    (1UL << 54)
#define PT_XN     (PT_PXN | PT_UXN)
#define PT_DEV    (0UL << 2)            /* MAIR attr 0: Device-nGnRnE */
#define PT_MEM    (1UL << 2)            /* MAIR attr 1: Normal */
#define PT_MAIR   (0x00UL | (0xFFUL << 8))
/* TCR_EL1: T0SZ=32, IRGN0/ORGN0 non-cacheable walks (caches stay off), TG0 4KB, EPD1, IPS 36-bit */
#define PT_TCR    (32UL | (1UL << 23) | (1UL << 32))

struct pt_layout {                      /* half-open [a, b) ranges, page aligned, all below 2 MB */
    pt_u64 rt_x0, rt_x1;                /* runtime code + rodata */
    pt_u64 op_x0, op_x1;                /* op code + svc stubs */
    pt_u64 op_r0, op_r1;                /* op rodata */
    pt_u64 op_rw0, op_guard, op_rw1;    /* op data, bss, [guard page], stack */
    pt_u64 rt_rw0, rt_rw1;              /* runtime bss + stack */
};
struct pt_dev { pt_u64 pa; pt_u64 ap; };

/* the op's windows (EL0) and the runtime's devices (EL1), 4 KB pages */
static const struct pt_dev PT_DEV_PAGES[] = {
    { 0xFE200000UL, PT_EL1RW },         /* GPIO — runtime (I2C and UART pins are set before the op) */
    { 0xFE804000UL, PT_EL1RW },         /* BSC1, I2C bus 1 — runtime (the op asks by svc) */
    { 0xFE201000UL, PT_EL1RW },         /* UART0 — runtime */
};
/* runtime-only devices kept as 2 MB blocks (no op window shares their 2 MB) */
static const pt_u64 PT_DEV_BLOCKS_EL1[] = {
    0xFE000000UL,                       /* PM watchdog (power off) */
    0xFF800000UL,                       /* GIC-400 (time budget interrupt) */
};
#define PT_NTABLES 6                    /* L1, L2 low GB, L2 top GB, 3 × L3 */

static pt_u64 pt_pa(const void *p) { return (pt_u64)p; }

/* t: PT_NTABLES zeroed, 4 KB-aligned tables. Returns 0, or -1 if the layout does not fit. */
static int pt_build(pt_u64 (*t)[512], const struct pt_layout *L) {
    pt_u64 *l1 = t[0], *l2lo = t[1], *l2hi = t[2];
    int next = 3;
    if (L->rt_rw1 > 0x200000UL) return -1;
    l1[0] = pt_pa(l2lo) | PT_TBL;
    l1[3] = pt_pa(l2hi) | PT_TBL;
    /* image: 0 .. 2 MB at 4 KB */
    pt_u64 *img = t[next++];
    l2lo[0] = pt_pa(img) | PT_TBL;
    for (pt_u64 pa = 0; pa < 0x200000UL; pa += 0x1000) {
        pt_u64 d = 0;
        if      (pa >= L->rt_x0  && pa < L->rt_x1)  d = PT_EL1RO | PT_UXN;
        else if (pa >= L->op_x0  && pa < L->op_x1)  d = PT_RO | PT_PXN;
        else if (pa >= L->op_r0  && pa < L->op_r1)  d = PT_RO | PT_XN;
        else if (pa >= L->op_rw0 && pa < L->op_rw1 && pa != L->op_guard) d = PT_RW | PT_XN;
        else if (pa >= L->rt_rw0 && pa < L->rt_rw1) d = PT_EL1RW | PT_XN;
        img[pa >> 12] = d ? (pa | d | PT_MEM | PT_ISH | PT_AF | PT_PAGE) : 0;
    }
    /* peripherals: top GB (0xC0000000..) */
    for (unsigned i = 0; i < sizeof PT_DEV_BLOCKS_EL1 / sizeof PT_DEV_BLOCKS_EL1[0]; i++) {
        pt_u64 pa = PT_DEV_BLOCKS_EL1[i];
        l2hi[(pa - 0xC0000000UL) >> 21] = pa | PT_EL1RW | PT_XN | PT_DEV | PT_AF | PT_BLOCK;
    }
    for (unsigned i = 0; i < sizeof PT_DEV_PAGES / sizeof PT_DEV_PAGES[0]; i++) {
        pt_u64 pa = PT_DEV_PAGES[i].pa, *e = &l2hi[(pa - 0xC0000000UL) >> 21], *l3;
        if ((*e & 3UL) == PT_BLOCK) return -1;                      /* window inside a runtime block */
        if (!*e) { if (next >= PT_NTABLES) return -1; l3 = t[next++]; *e = pt_pa(l3) | PT_TBL; }
        else l3 = (pt_u64 *)(*e & 0x0000FFFFFFFFF000UL);
        l3[(pa >> 12) & 511] = pa | PT_DEV_PAGES[i].ap | PT_XN | PT_DEV | PT_AF | PT_PAGE;
    }
    return 0;
}
#endif
