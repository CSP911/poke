/* First EL0 unit: proves a translated address space (VA != PA), static
 * state, stack, syscalls and a clean exit. Injected with `poke unit`. */
typedef unsigned long u64;
#define SYS_LOG 0
#define SYS_EXIT 1
static long sys(long n, long a, long b, long c) {
    register long x8 __asm__("x8") = n; register long x0 __asm__("x0") = a;
    register long x1 __asm__("x1") = b;  register long x2 __asm__("x2") = c;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory");
    return x0;
}
static int counter = 7;                          /* .data */
static char buf[64];                             /* .bss */
static void hex(char *b, u64 v) { const char *h = "0123456789abcdef"; for (int i = 0; i < 12; i++) b[i] = h[(v >> (44 - i*4)) & 0xf]; b[12] = 0; }
__attribute__((section(".text.main")))
void _start(void) {
    sys(SYS_LOG, (long)"hello from EL0", 0, 0);
    counter += 35;                               /* 42 if .data loaded and writable */
    u64 pc; __asm__ volatile("adr %0, ." : "=r"(pc));
    u64 sp; __asm__ volatile("mov %0, sp" : "=r"(sp));
    buf[0] = 'p'; buf[1] = 'c'; buf[2] = '='; hex(buf + 3, pc); buf[15] = ' '; buf[16] = 's'; buf[17] = 'p'; buf[18] = '='; hex(buf + 19, sp); buf[31] = 0;
    sys(SYS_LOG, (long)buf, 0, 0);
    volatile int acc = 0; for (int i = 0; i < 100000; i++) acc += i;   /* some work on the stack */
    sys(SYS_LOG, (long)"static+stack ok", 0, 0);
    sys(SYS_EXIT, counter, 0, 0);
    for (;;) {}
}
