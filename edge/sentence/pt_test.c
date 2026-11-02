/* Off-target test of the fence (pt.h): build the op's page table on the hub for a real
 * image layout and walk it like the MMU would, checking who may read/write/execute what.
 *   aarch64-elf-nm build/ok_tmp105.elf | ./build/pt_test
 * Without input it uses a fixed layout. No QEMU needed. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pt.h"

enum { R = 1, W = 2, X = 4 };
static pt_u64 (*T)[512];

/* the allowed access set for (va, el), or -1 if unmapped; *dev = Device attribute */
static int perm(pt_u64 va, int el, int *dev) {
    pt_u64 d = T[0][(va >> 30) & 3];
    if ((d & 3) != PT_TBL) return -1;
    d = ((pt_u64 *)(d & 0x0000FFFFFFFFF000UL))[(va >> 21) & 511];
    if (!(d & 1)) return -1;
    if ((d & 3) == PT_TBL) {
        d = ((pt_u64 *)(d & 0x0000FFFFFFFFF000UL))[(va >> 12) & 511];
        if ((d & 3) != PT_PAGE) return -1;
    }
    if (!(d & PT_AF)) return -1;
    if ((d & 0x0000FFFFFFFFF000UL & ~((d & 3) == PT_BLOCK ? 0x1FFFFFUL : 0xFFFUL)) != (va & ~((d & 3) == PT_BLOCK ? 0x1FFFFFUL : 0xFFFUL)))
        return -2;                                                  /* not identity */
    *dev = ((d >> 2) & 7) == 0;
    int ap = (d >> 6) & 3, p = 0;
    if (el == 0) { if (ap == 1) p = R | W; else if (ap == 3) p = R; if ((p & R) && !(d & PT_UXN)) p |= X; }
    else {
        p = (ap & 2) ? R : R | W;
        if (!(d & PT_PXN) && ap != 1 && !(p & W)) p |= X;          /* EL0-writable → PXN; WXN */
    }
    return p;
}

static int fails, checks;
static void expect(const char *what, pt_u64 va, int el, int want, int want_dev) {
    int dev = 0, p = perm(va, el, &dev);
    checks++;
    int ok = p == want && (want <= 0 || want_dev < 0 || dev == want_dev);
    if (!ok) fails++;
    printf("%s %-44s EL%d %#010lx  %s%s%s%s\n", ok ? "ok  " : "FAIL", what, el, va,
           p < 0 ? "unmapped" : (p & R) ? "r" : "-", p < 0 ? "" : (p & W) ? "w" : "-", p < 0 ? "" : (p & X) ? "x" : "-",
           p > 0 && dev ? " device" : "");
}

int main(void) {
    struct pt_layout L = { 0x80000, 0x82000, 0x82000, 0x83000, 0x83000, 0x84000, 0x84000, 0x85000, 0x8a000, 0x8a000, 0x95000 };
    char line[256]; int got = 0;
    while (fgets(line, sizeof line, stdin)) {
        unsigned long a; char t, n[128];
        if (sscanf(line, "%lx %c %127s", &a, &t, n) != 3) continue;
        got = 1;
        if (!strcmp(n, "__rt_text_start")) L.rt_x0 = a;
        if (!strcmp(n, "__rt_text_end")) L.rt_x1 = L.op_x0 = a;
        if (!strcmp(n, "__op_x_end")) L.op_x1 = L.op_r0 = a;
        if (!strcmp(n, "__op_r_end")) L.op_r1 = L.op_rw0 = a;
        if (!strcmp(n, "__op_guard")) L.op_guard = a;
        if (!strcmp(n, "__op_rw_end")) L.op_rw1 = L.rt_rw0 = a;
        if (!strcmp(n, "__rt_end")) L.rt_rw1 = a;
    }
    printf("layout%s: rt x [%#lx,%#lx) op x [%#lx,%#lx) op r [%#lx,%#lx) op rw [%#lx,%#lx) guard %#lx rt rw [%#lx,%#lx)\n",
           got ? " (from nm)" : " (fixed)", L.rt_x0, L.rt_x1, L.op_x0, L.op_x1, L.op_r0, L.op_r1, L.op_rw0, L.op_rw1, L.op_guard, L.rt_rw0, L.rt_rw1);
    T = aligned_alloc(4096, sizeof(pt_u64) * 512 * PT_NTABLES);
    memset(T, 0, sizeof(pt_u64) * 512 * PT_NTABLES);
    if (pt_build(T, &L)) { printf("FAIL pt_build refused the layout\n"); return 1; }

    expect("op code: run, not write",           L.op_x0,           0, R | X, 0);
    expect("op rodata: read only",              L.op_r0,           0, L.op_r1 > L.op_r0 ? R : -1, 0);
    expect("op data/bss: read/write, no exec",  L.op_rw0,          0, L.op_guard > L.op_rw0 ? R | W : -1, 0);
    expect("guard page below the op stack",     L.op_guard,        0, -1, -1);
    expect("op stack (top page)",               L.op_rw1 - 8,      0, R | W, 0);
    expect("runtime code",                      L.rt_x0,           0, 0, -1);
    expect("runtime bss/page tables/stack",     L.rt_rw0,          0, 0, -1);
    expect("runtime stack top",                 L.rt_rw1 - 8,      0, 0, -1);
    expect("below the image (firmware, spin tables)", 0x0,         0, -1, -1);
    expect("RAM above the image",               0x200000,          0, -1, -1);
    expect("RAM, 1 GB",                         0x40000000,        0, -1, -1);
    expect("GPIO (runtime's: pins set before the op)", 0xFE200000, 0, 0, -1);
    expect("GPIO at EL1",                       0xFE200000,        1, R | W, 1);
    expect("BSC1 (runtime's: I2C by svc)",      0xFE804000,        0, 0, -1);
    expect("BSC1 at EL1",                       0xFE804000,        1, R | W, 1);
    expect("UART0 (runtime's)",                 0xFE201000,        0, 0, -1);
    expect("PM watchdog (runtime's)",           0xFE100000,        0, 0, -1);
    expect("GIC distributor (runtime's)",       0xFF841000,        0, 0, -1);
    expect("BSC0 (I2C bus 0, not given)",       0xFE205000,        0, -1, -1);
    expect("page after BSC1",                   0xFE805000,        0, -1, -1);
    expect("EMMC2 / SD controller",             0xFE340000,        0, -1, -1);
    expect("DMA controller (in the PM block, EL1 only)", 0xFE007000, 0, 0, -1);
    expect("PCIe / xHCI",                       0xFD500000,        0, -1, -1);
    expect("runtime code at EL1: run",          L.rt_x0,           1, R | X, 0);
    expect("op code at EL1: never run",         L.op_x0,           1, R, 0);
    expect("op stack at EL1: no exec",          L.op_rw1 - 8,      1, R | W, 0);
    expect("runtime bss at EL1",                L.rt_rw0,          1, R | W, 0);
    expect("UART at EL1",                       0xFE201000,        1, R | W, 1);
    expect("PM at EL1",                         0xFE100000,        1, R | W, 1);
    expect("GIC CPU interface at EL1",          0xFF842000,        1, R | W, 1);
    printf("\n%d/%d fence checks as expected\n", checks - fails, checks);
    return fails != 0;
}
