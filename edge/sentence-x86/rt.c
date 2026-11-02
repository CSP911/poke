/* POKE sentence runtime, x86 PC (32-bit protected mode, loaded by network boot).
 *
 * One boot = one sentence: set up a GDT/TSS/IDT, a page table in which only the op's pages are
 * reachable from ring 3, a 1 kHz PIT tick, then drop to ring 3 at the op. The op talks to the
 * runtime through int 0x80 only (ustub.S). The runtime prints the same lines as the Pi runtime —
 * POKE-BOOT, POKE-SAY, POKE-FAULT, POKE-RESULT {json}, POKE-END ms=N status=, POKE-OFF — on
 * COM1, then zeroes everything the run used and resets the PC.
 */
typedef unsigned int u32; typedef unsigned short u16; typedef unsigned char u8;
typedef unsigned long long u64;

static inline void outb(u16 p, u8 v) { __asm__ volatile("outb %0, %1" :: "a"(v), "Nd"(p)); }
static inline u8 inb(u16 p) { u8 v; __asm__ volatile("inb %1, %0" : "=a"(v) : "Nd"(p)); return v; }

/* ── COM1 ── */
#define COM1 0x3F8
static void uart_init(void) {
    outb(COM1 + 1, 0); outb(COM1 + 3, 0x80); outb(COM1 + 0, 1); outb(COM1 + 1, 0);   /* 115200 */
    outb(COM1 + 3, 0x03); outb(COM1 + 2, 0xC7); outb(COM1 + 4, 0x03);
}
static void putc_(char c) { while (!(inb(COM1 + 5) & 0x20)) {} outb(COM1, (u8)c); }
static void puts_(const char *s) { while (*s) { if (*s == '\n') putc_('\r'); putc_(*s++); } }
static void putdec(long v) { char t[16]; int n = 0; unsigned long u = v < 0 ? (unsigned long)-v : (unsigned long)v;
    if (v < 0) { putc_('-'); }
    if (!u) { t[n++] = '0'; }
    while (u) { t[n++] = (char)('0' + u % 10); u /= 10; } while (n) putc_(t[--n]); }
static void puthex(u32 v) { puts_("0x"); for (int i = 28; i >= 0; i -= 4) putc_("0123456789abcdef"[(v >> i) & 15]); }
static void uart_drain(void) { while (!(inb(COM1 + 5) & 0x40)) {} }                /* transmitter empty */

/* ── time: PIT channel 0 at 1 kHz ── */
static volatile u32 ticks;
#define BUDGET_MS 20000u
static u32 deadline = BUDGET_MS;

/* ── result line (same rules as the Pi runtime: always valid JSON, "truncated" if it overflowed) ── */
static char res[1024]; static int rn, rcap = (int)sizeof res - 320, rfull; static int failed; static char why[128];
static void rc(char c) { if (rn < rcap) res[rn++] = c; else rfull = 1; }
static void rs(const char *s) { while (*s) { char c = *s++; if (c == '"' || c == '\\') rc('\\'); if ((u8)c >= 0x20) rc(c); } }
static void rkey(const char *k) { rc(rn ? ',' : '{'); rc('"'); rs(k); rc('"'); rc(':'); }
static void rdec(long v) { char t[16]; int n = 0; unsigned long u = v < 0 ? (unsigned long)-v : (unsigned long)v;
    if (v < 0) { rc('-'); }
    if (!u) { t[n++] = '0'; }
    while (u) { t[n++] = (char)('0' + u % 10); u /= 10; } while (n) rc(t[--n]); }
static void k_fail(const char *w) { failed = 1; int i = 0; for (; w[i] && i < (int)sizeof why - 1; i++) why[i] = w[i]; why[i] = 0; }

extern char __rt_text_end[], __op_x_start[], __op_x_end[], __op_r_end[], __op_guard[], __op_rw_start[],
            __op_rw_end[], __op_stack_top[], __rt_end[], __pt_dir[], __pt_tab[], __kstack_top[];
extern void enter_user(u32 eip, u32 esp);
extern void _op_entry(void);
extern __attribute__((noreturn)) void scrub_and_reset(u32 start, u32 end);
extern __attribute__((noreturn)) void scrub_and_return(u32 start, u32 end);
extern u32 nbp_ret;                                     /* set when started as a PXE program: go back, don't reset */
static __attribute__((noreturn)) void scrub_and_go(void) {
    if (nbp_ret) scrub_and_return((u32)__rt_text_end, (u32)__rt_end);
    scrub_and_reset((u32)__rt_text_end, (u32)__rt_end);
}

static int finishing;
__attribute__((noreturn)) static void finish(void) {
    __asm__ volatile("cli");
    if (finishing++) scrub_and_go();                    /* a fault while finishing: just go */
    rcap = (int)sizeof res - 2;
    if (rfull) { rkey("truncated"); rs("true"); }
    if (failed) { rkey("error"); rc('"'); rs(why); rc('"'); }
    if (!rn) rc('{');
    rc('}'); res[rn] = 0;
    puts_("POKE-RESULT "); puts_(res); puts_("\n");
    puts_("POKE-END ms="); putdec((long)ticks); puts_(failed ? " status=failed\n" : " status=ok\n");
    puts_("POKE-OFF\n");
    uart_drain();
    scrub_and_go();                                     /* op pages, runtime data, this stack: all zero */
}

/* ── reading what the op hands over: only bytes inside the op's own readable pages ── */
static int user_byte(u32 a) {
    if (a >= (u32)__op_x_start && a < (u32)__op_r_end) return 1;
    if (a >= (u32)__op_rw_start && a < (u32)__op_rw_end) return 1;
    return 0;
}
static void bad_pointer(void) {
    puts_("POKE-FAULT kind=pointer\n");
    k_fail("op passed a pointer outside its pages"); finish();
}
static void ustr(u32 a, char *dst, int max) {           /* no control bytes cross: a say() cannot start a line */
    int i = 0;
    for (; i < max - 1; i++) { if (!user_byte(a + (u32)i)) bad_pointer(); u8 c = *(volatile u8 *)(a + (u32)i); if (!c) break;
        dst[i] = (char)(c < 0x20 || c == 0x7f ? '?' : c); }
    dst[i] = 0;
}

/* ── the trap frame (boot.S trap_common: pusha after vector + error code) ── */
struct frame { u32 es, ds, edi, esi, ebp, esp_, ebx, edx, ecx, eax, vec, err, eip, cs, eflags, uesp, uss; };

static u32 sys(struct frame *f) {
    char k[64], s[200]; int m = rn; u32 a0 = f->ebx, a1 = f->ecx, a2 = f->edx;
    switch (f->eax) {
    case 0: finish();                                                   /* op_main returned */
    case 1: ustr(a0, s, sizeof s); puts_("POKE-SAY "); puts_(s); puts_("\n"); return 0;
    case 2: ustr(a0, k, sizeof k); rkey(k); rdec((long)a1); if (rfull) rn = m; return 0;
    case 3: {
        long v = (long)a1, p = 1; int d = (int)a2; if (d < 0) d = 0; if (d > 9) d = 9;
        for (int i = 0; i < d; i++) p *= 10;
        ustr(a0, k, sizeof k); rkey(k); if (v < 0) { rc('-'); v = -v; } rdec(v / p);
        if (d) { rc('.'); long fr = v % p; for (long q = p / 10; q; q /= 10) rc((char)('0' + (fr / q) % 10)); }
        if (rfull) rn = m;
        return 0; }
    case 4: ustr(a0, k, sizeof k); ustr(a1, s, sizeof s); rkey(k); rc('"'); rs(s); rc('"'); if (rfull) rn = m; return 0;
    case 5: ustr(a0, s, sizeof s); k_fail(s); return 0;
    case 6: return ticks;
    case 7: {
        if (a0 > 10000) {                                               /* never cut a wait short silently */
            puts_("POKE-FAULT kind=delay n="); putdec((long)a0); puts_("\n");
            k_fail("delay_ms over 10000 ms: one wait is at most 10 s and a run at most 20 s; longer waits belong to the hub");
            finish();
        }
        u32 end = ticks + a0;
        __asm__ volatile("sti");
        while (ticks < end && ticks < deadline) __asm__ volatile("hlt");
        __asm__ volatile("cli");
        if (ticks >= deadline) { puts_("POKE-FAULT kind=timeout\n"); k_fail("op ran past its time budget"); finish(); }
        return 0; }
    }
    puts_("POKE-FAULT kind=call nr="); putdec((long)f->eax); puts_("\n");
    k_fail("op made a call the runtime does not have"); finish();
    return 0;
}

static const char *const exc_name[32] = { "divide", "debug", "nmi", "breakpoint", "overflow", "bound", "invalid-opcode",
    "no-fpu", "double-fault", "", "tss", "segment", "stack", "protection", "page", "", "fpu", "alignment", "machine-check", "simd" };

void x86_trap(struct frame *f) {
    if (f->vec == 32) {                                                 /* PIT tick */
        ticks++;
        outb(0x20, 0x20);                                               /* EOI */
        if ((f->cs & 3) == 3 && ticks >= deadline) {
            puts_("POKE-FAULT kind=timeout\n"); k_fail("op ran past its time budget"); finish();
        }
        return;
    }
    if (f->vec == 0x80) { f->eax = sys(f); return; }
    u32 cr2; __asm__ volatile("mov %%cr2, %0" : "=r"(cr2));
    if ((f->cs & 3) == 3) {                                             /* the op faulted: report, end the run */
        puts_("POKE-FAULT kind="); puts_(f->vec < 20 && exc_name[f->vec][0] ? exc_name[f->vec] : "trap");
        puts_(" vec="); putdec((long)f->vec); puts_(" eip="); puthex(f->eip); puts_(" err="); puthex(f->err);
        if (f->vec == 14) { puts_(" addr="); puthex(cr2); }
        puts_("\n");
        k_fail(f->vec == 14 && cr2 >= (u32)__op_guard && cr2 < (u32)__op_rw_start ? "op overflowed its stack" :
               f->vec == 14 ? "op touched memory outside its pages" :
               f->vec == 13 ? "op used an instruction or port it may not (protection fault)" : "op faulted");
        finish();
    }
    puts_("POKE-FAULT kind=runtime vec="); putdec((long)f->vec); puts_(" eip="); puthex(f->eip); puts_("\n");
    k_fail("runtime fault"); finish();
}

/* ── descriptors ── */
struct gdte { u16 lim; u16 b0; u8 b1; u8 acc; u8 gran; u8 b2; } __attribute__((packed));
struct tss { u32 prev, esp0, ss0, rest[23]; u16 trap, iomap; } __attribute__((packed));
struct idte { u16 o0; u16 sel; u8 z; u8 type; u16 o1; } __attribute__((packed));
static struct gdte gdt[6]; static struct tss tss; static struct idte idt[256];
static struct { u16 lim; u32 base; } __attribute__((packed)) gdtr, idtr;

static void gset(int i, u32 base, u32 lim, u8 acc, u8 gran) {
    gdt[i] = (struct gdte){ (u16)lim, (u16)base, (u8)(base >> 16), acc, (u8)(((lim >> 16) & 0x0F) | gran), (u8)(base >> 24) };
}
static void iset(int v, void (*h)(void), u8 type) {
    u32 a = (u32)h; idt[v] = (struct idte){ (u16)a, 0x08, 0, type, (u16)(a >> 16) };
}
extern void (*trap_table[32])(void);
extern void irq0(void), syscall80(void);

static void descriptors(void) {
    gset(1, 0, 0xFFFFF, 0x9A, 0xC0);                    /* ring 0 code  0x08 */
    gset(2, 0, 0xFFFFF, 0x92, 0xC0);                    /* ring 0 data  0x10 */
    gset(3, 0, 0xFFFFF, 0xFA, 0xC0);                    /* ring 3 code  0x1B */
    gset(4, 0, 0xFFFFF, 0xF2, 0xC0);                    /* ring 3 data  0x23 */
    tss.ss0 = 0x10; tss.esp0 = (u32)__kstack_top; tss.iomap = sizeof tss;   /* no I/O bitmap: ring 3 has no ports */
    gset(5, (u32)&tss, sizeof tss - 1, 0x89, 0x00);     /* TSS 0x28 */
    gdtr.lim = sizeof gdt - 1; gdtr.base = (u32)gdt;
    __asm__ volatile("lgdt %0; ljmp $0x08, $1f; 1: mov $0x10, %%ax; mov %%ax, %%ds; mov %%ax, %%es; mov %%ax, %%ss;"
                     "mov %%ax, %%fs; mov %%ax, %%gs; mov $0x28, %%ax; ltr %%ax" :: "m"(gdtr) : "eax", "memory");
    for (int v = 0; v < 32; v++) iset(v, trap_table[v], 0x8E);   /* interrupt gates, ring 0 only */
    iset(32, irq0, 0x8E);
    iset(0x80, syscall80, 0xEE);                        /* ring 3 may call */
    idtr.lim = sizeof idt - 1; idtr.base = (u32)idt;
    __asm__ volatile("lidt %0" :: "m"(idtr));
}

/* ── paging: identity map 0–4 MiB; ring 3 reaches only the op's pages ── */
static void paging(void) {
    u32 *pd = (u32 *)__pt_dir, *pt = (u32 *)__pt_tab;
    for (u32 i = 0; i < 1024; i++) {
        u32 a = i << 12, f = 0x3;                                      /* present, writable, ring 0 */
        if (i == 0) f = 0;                                              /* null page: nothing */
        else if (a >= (u32)__op_x_start && a < (u32)__op_r_end) f = 0x5;           /* ring 3, read */
        else if (a >= (u32)__op_guard && a < (u32)__op_rw_start) f = 0;            /* guard */
        else if (a >= (u32)__op_rw_start && a < (u32)__op_rw_end) f = 0x7;         /* ring 3, read/write */
        pt[i] = a | f;
    }
    for (u32 i = 0; i < 1024; i++) pd[i] = 0;
    pd[0] = (u32)pt | 0x7;
    __asm__ volatile("mov %0, %%cr3; mov %%cr0, %%eax; or $0x80010000, %%eax; mov %%eax, %%cr0"   /* PG + WP */
                     :: "r"(pd) : "eax", "memory");
}

/* ── PIC + PIT ── */
static void timer(void) {
    outb(0x20, 0x11); outb(0xA0, 0x11); outb(0x21, 32); outb(0xA1, 40);
    outb(0x21, 4); outb(0xA1, 2); outb(0x21, 1); outb(0xA1, 1);
    outb(0x21, 0xFE); outb(0xA1, 0xFF);                                 /* only IRQ0 */
    u32 div = 1193182 / 1000;
    outb(0x43, 0x36); outb(0x40, (u8)div); outb(0x40, (u8)(div >> 8));
}

void x86_boot(void) {
    uart_init();
    descriptors();
    paging();
    timer();
    puts_("POKE-BOOT x86\n");
    enter_user((u32)_op_entry, (u32)__op_stack_top);
}
