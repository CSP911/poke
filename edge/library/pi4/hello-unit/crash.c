/* Isolation test: a unit that misbehaves must die alone — the kernel keeps running. */
typedef unsigned long u64;
static long sys(long n, long a, long b, long c) {
    register long x8 __asm__("x8") = n; register long x0 __asm__("x0") = a;
    register long x1 __asm__("x1") = b;  register long x2 __asm__("x2") = c;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory");
    return x0;
}
__attribute__((section(".text.main")))
void _start(void) {
    sys(0, (long)"about to read kernel memory at 0x80000 (should fault)", 0, 0);
    volatile u64 *k = (volatile u64 *)0x80000;          /* kernel image: mapped EL1-only */
    u64 v = *k;                                          /* → permission fault at EL0 */
    sys(0, (long)"UNREACHABLE: read the kernel!", v, 0);
    sys(1, 1, 0, 0);
}
