#ifndef POKE_IO_H
#define POKE_IO_H

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;

// ── x86 Port I/O ──
#ifdef __i386__
static inline u8  inb(u16 port) { u8 v; __asm__ volatile("inb %1,%0":"=a"(v):"Nd"(port)); return v; }
static inline u16 inw(u16 port) { u16 v; __asm__ volatile("inw %1,%0":"=a"(v):"Nd"(port)); return v; }
static inline u32 inl(u16 port) { u32 v; __asm__ volatile("inl %1,%0":"=a"(v):"Nd"(port)); return v; }
static inline void outb(u16 port, u8 val)  { __asm__ volatile("outb %0,%1"::"a"(val),"Nd"(port)); }
static inline void outw(u16 port, u16 val) { __asm__ volatile("outw %0,%1"::"a"(val),"Nd"(port)); }
static inline void outl(u16 port, u32 val) { __asm__ volatile("outl %0,%1"::"a"(val),"Nd"(port)); }
#endif

// ── MMIO helpers ──
static inline u32 mmio_read32(u32 addr) {
    return *(volatile u32 *)(unsigned long)addr;
}

static inline void mmio_write32(u32 addr, u32 val) {
    *(volatile u32 *)(unsigned long)addr = val;
}

static inline u16 mmio_read16(u32 addr) {
    return *(volatile u16 *)(unsigned long)addr;
}

static inline u8 mmio_read8(u32 addr) {
    return *(volatile u8 *)(unsigned long)addr;
}

// ── Delay (rough, busy-wait) ──
static inline void poke_delay(u32 cycles) {
    for (volatile u32 i = 0; i < cycles; i++);
}

#endif
