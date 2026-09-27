typedef unsigned long u64; typedef unsigned int u32;
static long sys(long n, long a, long b, long c) { register long x8 __asm__("x8") = n; register long x0 __asm__("x0") = a; register long x1 __asm__("x1") = b; register long x2 __asm__("x2") = c; __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory"); return x0; }
static void hex(char *b, u64 v) { const char *h = "0123456789abcdef"; for (int i = 0; i < 16; i++) b[i] = h[(v >> (60 - i*4)) & 0xf]; b[16] = 0; }
static char buf[32];
__attribute__((section(".text.main")))
void _start(void) {
    sys(0, (long)"mapme: reading PCIe bridge 0xFD500000", 0, 0);
    u32 v = *(volatile u32 *)0xFD500000UL; buf[0]='r'; buf[1]='='; hex(buf+2, v); sys(0, (long)buf, 0, 0);
    sys(0, (long)"mapme: reading window 0x02300000", 0, 0);
    u64 r0 = *(volatile u64 *)0x02300000UL; buf[0]='0'; buf[1]='='; hex(buf+2, r0); sys(0, (long)buf, 0, 0);
    sys(0, (long)"mapme: writing window 0x02300000", 0, 0);
    *(volatile u64 *)0x02300000UL = 0x1122334455667788UL;
    u64 w = *(volatile u64 *)0x02300000UL; buf[0]='w'; buf[1]='='; hex(buf+2, w); sys(0, (long)buf, 0, 0);
    sys(1, 0, 0, 0);
}
