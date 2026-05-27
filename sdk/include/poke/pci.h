#ifndef POKE_PCI_H
#define POKE_PCI_H

#include <poke/io.h>

#define PCI_CONFIG_ADDR 0xCF8
#define PCI_CONFIG_DATA 0xCFC

static inline u32 pci_read(u8 bus, u8 slot, u8 func, u8 offset) {
    u32 addr = 0x80000000 | (bus << 16) | (slot << 11) | (func << 8) | (offset & 0xFC);
    outl(PCI_CONFIG_ADDR, addr);
    return inl(PCI_CONFIG_DATA);
}

static inline u16 pci_vendor(u8 bus, u8 slot) {
    return pci_read(bus, slot, 0, 0) & 0xFFFF;
}

static inline u16 pci_device(u8 bus, u8 slot) {
    return (pci_read(bus, slot, 0, 0) >> 16) & 0xFFFF;
}

static inline u32 pci_bar0(u8 bus, u8 slot) {
    return pci_read(bus, slot, 0, 0x10) & 0xFFFFFFF0;
}

static inline u8 pci_class(u8 bus, u8 slot) {
    return (pci_read(bus, slot, 0, 0x08) >> 24) & 0xFF;
}

static inline u8 pci_subclass(u8 bus, u8 slot) {
    return (pci_read(bus, slot, 0, 0x08) >> 16) & 0xFF;
}

// Scan PCI bus, call callback for each device found
// callback returns: 0 = continue, 1 = stop
typedef int (*pci_scan_cb)(u8 bus, u8 slot, u16 vendor, u16 device, void *ctx);

static int pci_scan(pci_scan_cb cb, void *ctx) {
    int count = 0;
    for (int slot = 0; slot < 32; slot++) {
        u16 v = pci_vendor(0, slot);
        if (v == 0xFFFF || v == 0x0000) continue;
        u16 d = pci_device(0, slot);
        count++;
        if (cb && cb(0, slot, v, d, ctx)) break;
    }
    return count;
}

#endif
