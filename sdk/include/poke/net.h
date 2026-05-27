#ifndef POKE_NET_H
#define POKE_NET_H

#include <poke/io.h>

// e1000 register offsets
#define E1000_CTRL   0x0000
#define E1000_STATUS 0x0008
#define E1000_RAL    0x5400
#define E1000_RAH    0x5404

typedef struct {
    u32 base;  // MMIO base address
} e1000_t;

static inline void e1000_init(e1000_t *dev, u32 bar0) {
    dev->base = bar0;
}

static inline u32 e1000_read(e1000_t *dev, u32 reg) {
    return mmio_read32(dev->base + reg);
}

static inline void e1000_write(e1000_t *dev, u32 reg, u32 val) {
    mmio_write32(dev->base + reg, val);
}

static inline int e1000_link_up(e1000_t *dev) {
    return (e1000_read(dev, E1000_STATUS) >> 1) & 1;
}

static inline void e1000_read_mac(e1000_t *dev, u8 *mac) {
    u32 lo = e1000_read(dev, E1000_RAL);
    u32 hi = e1000_read(dev, E1000_RAH);
    mac[0] = lo & 0xFF;
    mac[1] = (lo >> 8) & 0xFF;
    mac[2] = (lo >> 16) & 0xFF;
    mac[3] = (lo >> 24) & 0xFF;
    mac[4] = hi & 0xFF;
    mac[5] = (hi >> 8) & 0xFF;
}

#endif
