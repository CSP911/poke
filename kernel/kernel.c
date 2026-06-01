/* ============================================
 * POKE OS — C Kernel
 * VGA + Keyboard + Shell + Network
 * ============================================ */

/* ── Types ── */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;

/* ── Port I/O ── */
static inline void outb(u16 port, u8 val) {
    __asm__ volatile("outb %0, %1" : : "a"(val), "Nd"(port));
}

static inline void outw(u16 port, u16 val) {
    __asm__ volatile("outw %0, %1" : : "a"(val), "Nd"(port));
}

static inline void outl(u16 port, u32 val) {
    __asm__ volatile("outl %0, %1" : : "a"(val), "Nd"(port));
}

static inline u8 inb(u16 port) {
    u8 ret;
    __asm__ volatile("inb %1, %0" : "=a"(ret) : "Nd"(port));
    return ret;
}

static inline u16 inw(u16 port) {
    u16 ret;
    __asm__ volatile("inw %1, %0" : "=a"(ret) : "Nd"(port));
    return ret;
}

static inline u32 inl(u16 port) {
    u32 ret;
    __asm__ volatile("inl %1, %0" : "=a"(ret) : "Nd"(port));
    return ret;
}

/* ── Serial Debug (COM1) ── */
void serial_init(void) {
    outb(0x3F8 + 1, 0x00); /* Disable interrupts */
    outb(0x3F8 + 3, 0x80); /* Enable DLAB */
    outb(0x3F8 + 0, 0x03); /* Baud 38400 */
    outb(0x3F8 + 1, 0x00);
    outb(0x3F8 + 3, 0x03); /* 8N1 */
    outb(0x3F8 + 2, 0xC7); /* FIFO */
    outb(0x3F8 + 4, 0x0B); /* IRQs enabled, RTS/DSR set */
}

void serial_putc(char c) {
    while (!(inb(0x3F8 + 5) & 0x20));
    outb(0x3F8, c);
}

void serial_print(const char *s) {
    while (*s) serial_putc(*s++);
}

void serial_hex(u32 val) {
    const char hex[] = "0123456789ABCDEF";
    serial_print("0x");
    for (int i = 28; i >= 0; i -= 4)
        serial_putc(hex[(val >> i) & 0xF]);
}

/* ── VGA Text Mode ── */
#define VGA_BASE 0xB8000
#define VGA_WIDTH 80
#define VGA_HEIGHT 25

static u16 *vga = (u16 *)VGA_BASE;
static int cursor_x = 0;
static int cursor_y = 0;
static u8 vga_color = 0x0F; /* white on black */

void vga_clear(void) {
    for (int i = 0; i < VGA_WIDTH * VGA_HEIGHT; i++)
        vga[i] = (0x07 << 8) | ' ';
    cursor_x = 0;
    cursor_y = 0;
}

void vga_scroll(void) {
    for (int i = 0; i < VGA_WIDTH * (VGA_HEIGHT - 1); i++)
        vga[i] = vga[i + VGA_WIDTH];
    for (int i = 0; i < VGA_WIDTH; i++)
        vga[VGA_WIDTH * (VGA_HEIGHT - 1) + i] = (0x07 << 8) | ' ';
    cursor_y = VGA_HEIGHT - 1;
}

void vga_putchar(char c) {
    if (c == '\n') {
        cursor_x = 0;
        cursor_y++;
    } else if (c == '\b') {
        if (cursor_x > 0) {
            cursor_x--;
            vga[cursor_y * VGA_WIDTH + cursor_x] = (vga_color << 8) | ' ';
        }
    } else {
        vga[cursor_y * VGA_WIDTH + cursor_x] = (vga_color << 8) | c;
        cursor_x++;
    }
    if (cursor_x >= VGA_WIDTH) { cursor_x = 0; cursor_y++; }
    if (cursor_y >= VGA_HEIGHT) vga_scroll();
}

void vga_print(const char *s) {
    while (*s) vga_putchar(*s++);
}

void vga_print_hex(u32 val) {
    const char hex[] = "0123456789ABCDEF";
    vga_print("0x");
    for (int i = 28; i >= 0; i -= 4)
        vga_putchar(hex[(val >> i) & 0xF]);
}

void vga_print_dec(u32 val) {
    char buf[12];
    int i = 0;
    if (val == 0) { vga_putchar('0'); return; }
    while (val > 0) { buf[i++] = '0' + (val % 10); val /= 10; }
    while (i > 0) vga_putchar(buf[--i]);
}

void vga_set_color(u8 fg, u8 bg) {
    vga_color = (bg << 4) | fg;
}

/* ── Keyboard (Polling) ── */
static const char scancode_table[58] = {
    0, 27, '1','2','3','4','5','6','7','8','9','0','-','=',
    '\b', 0, 'q','w','e','r','t','y','u','i','o','p','[',']',
    '\r', 0, 'a','s','d','f','g','h','j','k','l',';','\'','`',
    0, '\\', 'z','x','c','v','b','n','m',',','.','/',
    0, 0, 0, ' '
};

/* Keyboard ring buffer — kernel pushes scancodes, injected code reads.
 * Injected code accesses via result_buf: after execution,
 * kernel copies latest scancode count + last key into result. */
#define KB_BUF_SIZE  256

static volatile u32 kb_write_idx = 0;
static volatile u8  kb_ring[KB_BUF_SIZE];

static void kb_buf_init(void) {
    kb_write_idx = 0;
    for (int i = 0; i < KB_BUF_SIZE; i++) kb_ring[i] = 0;
}

static void __attribute__((noinline)) kb_buf_push(u8 scancode) {
    kb_ring[kb_write_idx % KB_BUF_SIZE] = scancode;
    kb_write_idx++;
}

char kb_read(void) {
    u8 status = inb(0x64);
    if (!(status & 1)) return 0;

    u8 code = inb(0x60);

    /* Always push raw scancode to ring buffer (for injected code) */
    kb_buf_push(code);

    if (code & 0x80) return 0; /* release */
    if (code >= 58) return 0;
    return scancode_table[code];
}

/* ── String Utils ── */
int str_eq(const char *a, const char *b) {
    while (*a && *b) { if (*a++ != *b++) return 0; }
    return *a == *b;
}

int str_len(const char *s) {
    int n = 0; while (*s++) n++; return n;
}

void mem_copy(void *dst, const void *src, int n) {
    u8 *d = dst; const u8 *s = src;
    while (n--) *d++ = *s++;
}

void mem_set(void *dst, u8 val, int n) {
    u8 *d = dst;
    while (n--) *d++ = val;
}

/* ── PCI ── */
u32 pci_read(u8 bus, u8 slot, u8 func, u8 offset) {
    u32 addr = (1 << 31) | (bus << 16) | (slot << 11) | (func << 8) | (offset & 0xFC);
    outl(0xCF8, addr);
    return inl(0xCFC);
}

void pci_write(u8 bus, u8 slot, u8 func, u8 offset, u32 val) {
    u32 addr = (1 << 31) | (bus << 16) | (slot << 11) | (func << 8) | (offset & 0xFC);
    outl(0xCF8, addr);
    outl(0xCFC, val);
}

/* ── E1000 Network Driver ── */
#define E1000_VENDOR 0x8086
#define E1000_DEVICE 0x100E  /* 82540EM */

#define E1000_CTRL    0x0000
#define E1000_STATUS  0x0008
#define E1000_RCTL    0x0100
#define E1000_TCTL    0x0400
#define E1000_RDBAL   0x2800
#define E1000_RDBAH   0x2804
#define E1000_RDLEN   0x2808
#define E1000_RDH     0x2810
#define E1000_RDT     0x2818
#define E1000_TDBAL   0x3800
#define E1000_TDBAH   0x3804
#define E1000_TDLEN   0x3808
#define E1000_TDH     0x3810
#define E1000_TDT     0x3818
#define E1000_RAL     0x5400
#define E1000_RAH     0x5404

#define RX_DESC_COUNT 32
#define TX_DESC_COUNT 8
#define PKT_BUF_SIZE  2048

struct rx_desc {
    u32 addr_lo;
    u32 addr_hi;
    u16 length;
    u16 checksum;
    u8  status;
    u8  errors;
    u16 special;
} __attribute__((packed));

struct tx_desc {
    u32 addr_lo;
    u32 addr_hi;
    u16 length;
    u8  cso;
    u8  cmd;
    u8  status;
    u8  css;
    u16 special;
} __attribute__((packed));

static volatile u32 e1000_base = 0;
static struct rx_desc rx_descs[RX_DESC_COUNT] __attribute__((aligned(16)));
static struct tx_desc tx_descs[TX_DESC_COUNT] __attribute__((aligned(16)));
static u8 rx_buffers[RX_DESC_COUNT][PKT_BUF_SIZE] __attribute__((aligned(16)));
static u8 tx_buffers[TX_DESC_COUNT][PKT_BUF_SIZE] __attribute__((aligned(16)));
static int rx_cur = 0;
static int tx_cur = 0;
static u8 my_mac[6];

/* IP config */
static u8 my_ip[4] = {10, 0, 2, 15};     /* QEMU user-mode default guest IP */
static u8 gw_ip[4] = {10, 0, 2, 2};      /* QEMU gateway */
static u8 gw_mac[6] = {0,0,0,0,0,0};
static u8 hub_mac[6] = {0};
static int hub_mac_resolved = 0;

static void e1000_write(u32 reg, u32 val) {
    *(volatile u32 *)(e1000_base + reg) = val;
}

static u32 e1000_read(u32 reg) {
    return *(volatile u32 *)(e1000_base + reg);
}

int e1000_find(void) {
    for (int bus = 0; bus < 256; bus++) {
        for (int slot = 0; slot < 32; slot++) {
            u32 id = pci_read(bus, slot, 0, 0);
            u16 vendor = id & 0xFFFF;
            u16 device = (id >> 16) & 0xFFFF;

            if (vendor == E1000_VENDOR && device == E1000_DEVICE) {
                /* Get BAR0 (MMIO base) */
                u32 bar0 = pci_read(bus, slot, 0, 0x10);
                e1000_base = bar0 & 0xFFFFFFF0;

                /* Enable bus mastering + memory space */
                u32 cmd = pci_read(bus, slot, 0, 0x04);
                cmd |= (1 << 2) | (1 << 1); /* bus master + memory space */
                pci_write(bus, slot, 0, 0x04, cmd);

                return 1;
            }
        }
    }
    return 0;
}

void e1000_init(void) {
    /* Reset */
    u32 ctrl = e1000_read(E1000_CTRL);
    e1000_write(E1000_CTRL, ctrl | (1 << 26));
    for (volatile int i = 0; i < 1000000; i++); /* longer delay after reset */

    /* Clear interrupts */
    e1000_read(0x00C0); /* ICR - clear pending interrupts */

    /* Read MAC from EEPROM or RAL/RAH */
    u32 ral = e1000_read(E1000_RAL);
    u32 rah = e1000_read(E1000_RAH);

    /* If RAL is zero, use default MAC */
    if (ral == 0) {
        my_mac[0] = 0x52; my_mac[1] = 0x54; my_mac[2] = 0x00;
        my_mac[3] = 0x12; my_mac[4] = 0x34; my_mac[5] = 0x56;
        e1000_write(E1000_RAL, my_mac[0] | (my_mac[1]<<8) | (my_mac[2]<<16) | (my_mac[3]<<24));
        e1000_write(E1000_RAH, my_mac[4] | (my_mac[5]<<8) | (1<<31));
    } else {
        my_mac[0] = ral & 0xFF;
        my_mac[1] = (ral >> 8) & 0xFF;
        my_mac[2] = (ral >> 16) & 0xFF;
        my_mac[3] = (ral >> 24) & 0xFF;
        my_mac[4] = rah & 0xFF;
        my_mac[5] = (rah >> 8) & 0xFF;
        /* Ensure AV bit is set */
        e1000_write(E1000_RAH, rah | (1 << 31));
    }

    /* Clear multicast table */
    for (int i = 0; i < 128; i++)
        e1000_write(0x5200 + i * 4, 0);

    /* Setup RX descriptors */
    for (int i = 0; i < RX_DESC_COUNT; i++) {
        rx_descs[i].addr_lo = (u32)&rx_buffers[i];
        rx_descs[i].addr_hi = 0;
        rx_descs[i].status = 0;
    }
    e1000_write(E1000_RDBAL, (u32)rx_descs);
    e1000_write(E1000_RDBAH, 0);
    e1000_write(E1000_RDLEN, RX_DESC_COUNT * sizeof(struct rx_desc));
    e1000_write(E1000_RDH, 0);
    e1000_write(E1000_RDT, RX_DESC_COUNT - 1);

    /* RCTL: EN + SBP + UPE + MPE + BAM + BSIZE=2048 + SECRC */
    e1000_write(E1000_RCTL,
        (1 << 1) |   /* EN - Receiver Enable */
        (1 << 2) |   /* SBP - Store Bad Packets (for debug) */
        (1 << 3) |   /* UPE - Unicast Promiscuous */
        (1 << 4) |   /* MPE - Multicast Promiscuous */
        (1 << 15) |  /* BAM - Broadcast Accept */
        (0 << 16) |  /* BSIZE = 2048 (00) */
        (1 << 25) |  /* BSIZE extension */
        (1 << 26)    /* SECRC - Strip CRC */
    );

    /* Setup TX descriptors */
    for (int i = 0; i < TX_DESC_COUNT; i++) {
        tx_descs[i].addr_lo = (u32)&tx_buffers[i];
        tx_descs[i].addr_hi = 0;
        tx_descs[i].status = 1; /* DD - done */
        tx_descs[i].cmd = 0;
    }
    e1000_write(E1000_TDBAL, (u32)tx_descs);
    e1000_write(E1000_TDBAH, 0);
    e1000_write(E1000_TDLEN, TX_DESC_COUNT * sizeof(struct tx_desc));
    e1000_write(E1000_TDH, 0);
    e1000_write(E1000_TDT, 0);

    /* TCTL: EN + PSP + CT=15 + COLD=64 */
    e1000_write(E1000_TCTL,
        (1 << 1) |   /* EN */
        (1 << 3) |   /* PSP - Pad Short Packets */
        (15 << 4) |  /* CT - Collision Threshold */
        (64 << 12)   /* COLD - Collision Distance */
    );

    /* Set link up */
    ctrl = e1000_read(E1000_CTRL);
    ctrl |= (1 << 6);  /* SLU - Set Link Up */
    ctrl &= ~(1 << 3); /* Clear LRST */
    ctrl &= ~(1 << 31);/* Clear PHY_RST */
    e1000_write(E1000_CTRL, ctrl);
}

void e1000_send(u8 *data, u16 len) {
    /* Copy data to TX buffer */
    mem_copy(tx_buffers[tx_cur], data, len);

    tx_descs[tx_cur].addr_lo = (u32)&tx_buffers[tx_cur];
    tx_descs[tx_cur].addr_hi = 0;
    tx_descs[tx_cur].length = len;
    tx_descs[tx_cur].cmd = (1 << 0) | (1 << 1) | (1 << 3); /* EOP + IFCS + RS */
    tx_descs[tx_cur].status = 0;

    int old = tx_cur;
    tx_cur = (tx_cur + 1) % TX_DESC_COUNT;
    e1000_write(E1000_TDT, tx_cur);

    /* Wait with timeout */
    for (int t = 0; t < 1000000; t++) {
        if (tx_descs[old].status & 1) return; /* DD set = done */
    }
    /* Timeout - continue anyway */
}

int e1000_recv(u8 *buf) {
    if (!(rx_descs[rx_cur].status & 1)) return 0; /* not ready */

    int len = rx_descs[rx_cur].length;
    mem_copy(buf, rx_buffers[rx_cur], len);

    rx_descs[rx_cur].status = 0;
    int old = rx_cur;
    rx_cur = (rx_cur + 1) % RX_DESC_COUNT;
    e1000_write(E1000_RDT, old);

    return len;
}

/* ══════════════════════════════════════════
 * NE2000 NIC Driver (for v86 browser emulator)
 * I/O port based, much simpler than e1000
 * ══════════════════════════════════════════ */

#define NE2K_VENDOR  0x10EC
#define NE2K_DEVICE  0x8029
/* v86 also uses Realtek 8029 (same as NE2000 PCI) */

static u16 ne2k_iobase = 0;
static int nic_is_ne2k = 0;  /* 0 = e1000, 1 = ne2000 */

/* NE2000 register offsets */
#define NE2K_CR      0x00  /* Command Register */
#define NE2K_PSTART  0x01  /* Page Start (write, page 0) */
#define NE2K_PSTOP   0x02  /* Page Stop (write, page 0) */
#define NE2K_BNRY    0x03  /* Boundary Pointer */
#define NE2K_TSR     0x04  /* Transmit Status (read, page 0) */
#define NE2K_TPSR    0x04  /* Transmit Page Start (write, page 0) */
#define NE2K_TBCR0   0x05  /* Transmit Byte Count 0 */
#define NE2K_TBCR1   0x06  /* Transmit Byte Count 1 */
#define NE2K_ISR     0x07  /* Interrupt Status */
#define NE2K_RSAR0   0x08  /* Remote Start Address 0 */
#define NE2K_RSAR1   0x09  /* Remote Start Address 1 */
#define NE2K_RBCR0   0x0A  /* Remote Byte Count 0 */
#define NE2K_RBCR1   0x0B  /* Remote Byte Count 1 */
#define NE2K_RCR     0x0C  /* Receive Configuration */
#define NE2K_TCR     0x0D  /* Transmit Configuration */
#define NE2K_DCR     0x0E  /* Data Configuration */
#define NE2K_IMR     0x0F  /* Interrupt Mask */
#define NE2K_CURR    0x07  /* Current Page (read, page 1) */
#define NE2K_DATA    0x10  /* Data port (DMA) */
#define NE2K_RESET   0x1F  /* Reset port */

/* Ring buffer: pages 0x46-0x80 for RX, 0x40-0x46 for TX */
#define NE2K_TX_START  0x40
#define NE2K_RX_START  0x46
#define NE2K_RX_STOP   0x80

static u8 ne2k_next_pkt = NE2K_RX_START;

int ne2k_find(void) {
    for (int bus = 0; bus < 256; bus++) {
        for (int slot = 0; slot < 32; slot++) {
            u32 id = pci_read(bus, slot, 0, 0);
            u16 vendor = id & 0xFFFF;
            u16 device = (id >> 16) & 0xFFFF;

            if (vendor == NE2K_VENDOR && device == NE2K_DEVICE) {
                u32 bar0 = pci_read(bus, slot, 0, 0x10);
                ne2k_iobase = bar0 & 0xFFFC;  /* I/O port */

                /* Enable bus mastering + I/O space */
                u32 cmd = pci_read(bus, slot, 0, 0x04);
                cmd |= (1 << 0) | (1 << 2);
                pci_write(bus, slot, 0, 0x04, cmd);

                return 1;
            }
        }
    }
    return 0;
}

void ne2k_init(void) {
    u16 base = ne2k_iobase;

    /* Stop NIC, select page 0 */
    outb(base + NE2K_CR, 0x21);  /* STP + DMA abort + page 0 */

    /* DCR: word transfer, normal mode, FIFO=8 */
    outb(base + NE2K_DCR, 0x49);

    /* Clear remote byte count */
    outb(base + NE2K_RBCR0, 0x00);
    outb(base + NE2K_RBCR1, 0x00);

    /* RCR: accept broadcast + multicast */
    outb(base + NE2K_RCR, 0x0C);

    /* TCR: internal loopback (for init) */
    outb(base + NE2K_TCR, 0x02);

    /* Setup ring buffer */
    outb(base + NE2K_PSTART, NE2K_RX_START);
    outb(base + NE2K_PSTOP, NE2K_RX_STOP);
    outb(base + NE2K_BNRY, NE2K_RX_START);

    /* Clear ISR */
    outb(base + NE2K_ISR, 0xFF);

    /* IMR: enable all interrupts */
    outb(base + NE2K_IMR, 0x00);

    /* Read MAC from NE2000 PROM (first 6 bytes at address 0) */
    outb(base + NE2K_CR, 0x0A);    /* remote read, start */
    outb(base + NE2K_RBCR0, 32);   /* 32 bytes */
    outb(base + NE2K_RBCR1, 0);
    outb(base + NE2K_RSAR0, 0);    /* address 0 */
    outb(base + NE2K_RSAR1, 0);
    outb(base + NE2K_CR, 0x0A);    /* start remote read DMA */

    for (int i = 0; i < 6; i++) {
        my_mac[i] = inb(base + NE2K_DATA);
        inb(base + NE2K_DATA);  /* skip duplicate byte (word mode) */
    }

    /* Set Physical Address (page 1) */
    outb(base + NE2K_CR, 0x61);  /* STP + page 1 */
    for (int i = 0; i < 6; i++) {
        outb(base + 0x01 + i, my_mac[i]);  /* PAR0-PAR5 */
    }

    /* Set multicast filter to accept all */
    for (int i = 0; i < 8; i++) {
        outb(base + 0x08 + i, 0xFF);  /* MAR0-MAR7 */
    }

    /* Set current page pointer */
    outb(base + NE2K_CURR, NE2K_RX_START + 1);

    /* Back to page 0, start NIC */
    outb(base + NE2K_CR, 0x22);  /* STA + DMA abort + page 0 */

    /* TCR: normal operation */
    outb(base + NE2K_TCR, 0x00);

    ne2k_next_pkt = NE2K_RX_START + 1;
}

void ne2k_send(u8 *data, u16 len) {
    u16 base = ne2k_iobase;

    /* Write packet to NE2000 TX buffer via remote DMA */
    outb(base + NE2K_CR, 0x22);   /* page 0, start, DMA abort */
    outb(base + NE2K_ISR, 0xFF);  /* clear ISR */

    /* Set remote DMA address and count */
    outb(base + NE2K_RSAR0, 0x00);
    outb(base + NE2K_RSAR1, NE2K_TX_START);
    outb(base + NE2K_RBCR0, len & 0xFF);
    outb(base + NE2K_RBCR1, (len >> 8) & 0xFF);

    /* Start remote write DMA */
    outb(base + NE2K_CR, 0x12);  /* STA + remote write */

    /* Write data word by word */
    for (int i = 0; i < len; i += 2) {
        u16 word = data[i];
        if (i + 1 < len) word |= (data[i+1] << 8);
        outw(base + NE2K_DATA, word);
    }

    /* Wait for DMA complete */
    while (!(inb(base + NE2K_ISR) & 0x40));
    outb(base + NE2K_ISR, 0x40);

    /* Set TX page start and byte count */
    outb(base + NE2K_TPSR, NE2K_TX_START);
    outb(base + NE2K_TBCR0, len & 0xFF);
    outb(base + NE2K_TBCR1, (len >> 8) & 0xFF);

    /* Transmit */
    outb(base + NE2K_CR, 0x26);  /* STA + TXP + DMA abort */

    /* Wait for transmit complete */
    for (int t = 0; t < 1000000; t++) {
        if (inb(base + NE2K_ISR) & 0x02) break;  /* PTX */
    }
    outb(base + NE2K_ISR, 0x02);
}

int ne2k_recv(u8 *buf) {
    u16 base = ne2k_iobase;

    /* Check if there's a packet */
    outb(base + NE2K_CR, 0x62);  /* page 1 */
    u8 curr = inb(base + NE2K_CURR);
    outb(base + NE2K_CR, 0x22);  /* page 0 */

    if (ne2k_next_pkt == curr) return 0;  /* no packet */

    /* Read 4-byte NE2000 header via remote DMA (word mode) */
    outb(base + NE2K_RSAR0, 0x00);
    outb(base + NE2K_RSAR1, ne2k_next_pkt);
    outb(base + NE2K_RBCR0, 4);
    outb(base + NE2K_RBCR1, 0);
    outb(base + NE2K_CR, 0x0A);  /* remote read */

    u16 w0 = inw(base + NE2K_DATA);  /* rsr + next_page */
    u16 w1 = inw(base + NE2K_DATA);  /* pkt_len (little-endian) */
    u8 next = (w0 >> 8) & 0xFF;
    u16 pkt_len = w1;

    /* Sanity check */
    pkt_len -= 4;  /* subtract header */
    if (pkt_len > PKT_BUF_SIZE) pkt_len = PKT_BUF_SIZE;

    /* Read packet data */
    outb(base + NE2K_RSAR0, 4);
    outb(base + NE2K_RSAR1, ne2k_next_pkt);
    outb(base + NE2K_RBCR0, pkt_len & 0xFF);
    outb(base + NE2K_RBCR1, (pkt_len >> 8) & 0xFF);
    outb(base + NE2K_CR, 0x0A);  /* remote read */

    for (int i = 0; i < pkt_len; i += 2) {
        u16 word = inw(base + NE2K_DATA);
        buf[i] = word & 0xFF;
        if (i + 1 < pkt_len) buf[i+1] = (word >> 8) & 0xFF;
    }

    /* Update boundary */
    ne2k_next_pkt = next;
    if (next == NE2K_RX_START) next = NE2K_RX_STOP;
    outb(base + NE2K_BNRY, next - 1);

    return pkt_len;
}

/* ── Unified NIC interface ── */
static void nic_send(u8 *data, u16 len) {
    if (nic_is_ne2k) ne2k_send(data, len);
    else e1000_send(data, len);
}

static int nic_recv(u8 *buf) {
    if (nic_is_ne2k) return ne2k_recv(buf);
    else return e1000_recv(buf);
}

/* ── Ethernet ── */
struct eth_header {
    u8 dst[6];
    u8 src[6];
    u16 type;
} __attribute__((packed));

#define ETH_ARP 0x0608
#define ETH_IP  0x0008

/* ── ARP ── */
struct arp_packet {
    u16 hw_type;
    u16 proto_type;
    u8 hw_len;
    u8 proto_len;
    u16 opcode;
    u8 sender_mac[6];
    u8 sender_ip[4];
    u8 target_mac[6];
    u8 target_ip[4];
} __attribute__((packed));

void handle_arp(u8 *pkt, int len) {
    struct eth_header *eth = (struct eth_header *)pkt;
    struct arp_packet *arp = (struct arp_packet *)(pkt + 14);

    /* Debug: show ARP details on screen line 2 */
    u16 *adbg = (u16 *)(VGA_BASE + (VGA_WIDTH * 2) * 2);
    adbg[0] = (0x0E<<8)|'A';
    adbg[1] = (0x0E<<8)|('0' + ((arp->opcode >> 8) & 0xF)); /* opcode */
    adbg[2] = (0x0E<<8)|' ';
    /* Show target IP */
    adbg[3] = (0x0E<<8)|('0' + arp->target_ip[0] / 100);
    adbg[4] = (0x0E<<8)|('0' + (arp->target_ip[0] / 10) % 10);
    adbg[5] = (0x0E<<8)|('0' + arp->target_ip[0] % 10);
    adbg[6] = (0x0E<<8)|'.';
    adbg[7] = (0x0E<<8)|('0' + arp->target_ip[1] % 10);
    adbg[8] = (0x0E<<8)|'.';
    adbg[9] = (0x0E<<8)|('0' + arp->target_ip[2] % 10);
    adbg[10] = (0x0E<<8)|'.';
    adbg[11] = (0x0E<<8)|('0' + arp->target_ip[3] / 10);
    adbg[12] = (0x0E<<8)|('0' + arp->target_ip[3] % 10);
    /* Show sender IP */
    adbg[14] = (0x0E<<8)|'f';
    adbg[15] = (0x0E<<8)|('0' + arp->sender_ip[0] / 100);
    adbg[16] = (0x0E<<8)|('0' + (arp->sender_ip[0] / 10) % 10);
    adbg[17] = (0x0E<<8)|('0' + arp->sender_ip[0] % 10);
    adbg[18] = (0x0E<<8)|'.';
    adbg[19] = (0x0E<<8)|('0' + arp->sender_ip[3] / 10);
    adbg[20] = (0x0E<<8)|('0' + arp->sender_ip[3] % 10);

    if (arp->opcode == 0x0100) { /* ARP request (network byte order) */
        /* Only respond if asking for 10.0.2.15 */
        if (arp->target_ip[0] == 10 && arp->target_ip[1] == 0 &&
            arp->target_ip[2] == 2 && arp->target_ip[3] == 15) {

            u8 reply[60];
            mem_set(reply, 0, 60);

            reply[0] = arp->sender_mac[0]; reply[1] = arp->sender_mac[1];
            reply[2] = arp->sender_mac[2]; reply[3] = arp->sender_mac[3];
            reply[4] = arp->sender_mac[4]; reply[5] = arp->sender_mac[5];
            reply[6] = my_mac[0]; reply[7] = my_mac[1];
            reply[8] = my_mac[2]; reply[9] = my_mac[3];
            reply[10] = my_mac[4]; reply[11] = my_mac[5];
            reply[12] = 0x08; reply[13] = 0x06;

            reply[14] = 0x00; reply[15] = 0x01;
            reply[16] = 0x08; reply[17] = 0x00;
            reply[18] = 6; reply[19] = 4;
            reply[20] = 0x00; reply[21] = 0x02;

            reply[22] = my_mac[0]; reply[23] = my_mac[1];
            reply[24] = my_mac[2]; reply[25] = my_mac[3];
            reply[26] = my_mac[4]; reply[27] = my_mac[5];
            reply[28] = 10; reply[29] = 0; reply[30] = 2; reply[31] = 15;

            reply[32] = arp->sender_mac[0]; reply[33] = arp->sender_mac[1];
            reply[34] = arp->sender_mac[2]; reply[35] = arp->sender_mac[3];
            reply[36] = arp->sender_mac[4]; reply[37] = arp->sender_mac[5];
            reply[38] = arp->sender_ip[0]; reply[39] = arp->sender_ip[1];
            reply[40] = arp->sender_ip[2]; reply[41] = arp->sender_ip[3];

            nic_send(reply, 60);

            /* Debug: mark reply sent */
            u16 *rdbg = (u16 *)(VGA_BASE + (VGA_WIDTH * 2 + 30) * 2);
            rdbg[0] = (0x0A<<8)|'R';
            rdbg[1] = (0x0A<<8)|'P';
            rdbg[2] = (0x0A<<8)|'L';
            rdbg[3] = (0x0A<<8)|'Y';

            /* Save gateway MAC */
            if (arp->sender_ip[0] == gw_ip[0] && arp->sender_ip[1] == gw_ip[1]) {
                mem_copy(gw_mac, arp->sender_mac, 6);
            }
        }
    }
    if (arp->opcode == 0x0200) { /* ARP reply */
        mem_copy(gw_mac, arp->sender_mac, 6);
        /* Also cache as hub MAC for monitor event delivery */
        mem_copy(hub_mac, arp->sender_mac, 6);
        hub_mac_resolved = 1;
    }
}

/* ── IP ── */
struct ip_header {
    u8 ver_ihl;
    u8 tos;
    u16 total_len;
    u16 id;
    u16 flags_frag;
    u8 ttl;
    u8 protocol;
    u16 checksum;
    u8 src[4];
    u8 dst[4];
} __attribute__((packed));

u16 ip_checksum(void *data, int len) {
    u32 sum = 0;
    u16 *p = (u16 *)data;
    while (len > 1) { sum += *p++; len -= 2; }
    if (len) sum += *(u8 *)p;
    while (sum >> 16) sum = (sum & 0xFFFF) + (sum >> 16);
    return ~sum;
}

/* ── TCP ── */
struct tcp_header {
    u16 src_port;
    u16 dst_port;
    u32 seq;
    u32 ack;
    u8 data_offset;
    u8 flags;
    u16 window;
    u16 checksum;
    u16 urgent;
} __attribute__((packed));

#define TCP_FIN 0x01
#define TCP_SYN 0x02
#define TCP_RST 0x04
#define TCP_PSH 0x08
#define TCP_ACK 0x10

/* TCP state */
static u32 tcp_local_seq = 1000;
static u32 tcp_remote_seq = 0;
static int tcp_connected = 0;
static u16 tcp_local_port = 80;

/* Forward declarations */
void tcp_send(u8 *dst_mac, u8 *dst_ip, u16 dst_port, u8 flags, u8 *data, int data_len);

/* Received data buffer */
#define HTTP_BUF_SIZE 40960
static u8 http_buf[HTTP_BUF_SIZE];
static int http_buf_len = 0;

/* Code execution buffer */
#define CODE_BUF_SIZE 4096
static u8 code_buf[CODE_BUF_SIZE] __attribute__((aligned(4096)));

/* ── Monitor System (autonomous event loop) ── */
#define MAX_MONITORS 4
#define MONITOR_CODE_SIZE 512

#define MON_OP_GT 0
#define MON_OP_LT 1
#define MON_OP_EQ 2
#define MON_OP_NE 3

typedef struct {
    u8  active;
    u8  code[MONITOR_CODE_SIZE];
    u32 code_len;
    u32 interval_ticks;         /* check interval in poll cycles */
    u32 ticks_remaining;        /* countdown to next check */
    u8  condition_op;           /* GT, LT, EQ, NE */
    u32 condition_val;          /* threshold value */
    u32 hub_ip_addr;            /* hub IP as u32 (LE) */
    u16 hub_port;               /* hub port */
    u32 trigger_count;          /* total triggers */
    u32 last_value;             /* last measured value */
    u8  fired;                  /* pending event flag */
} monitor_t;

static monitor_t monitors[MAX_MONITORS];
static int pending_event = -1;
static u32 pending_event_value = 0;
static u32 monitor_poll_counter = 0;

/* MON packet magic: "MON\0" = 0x004E4F4D */
#define MON_MAGIC_0 'M'
#define MON_MAGIC_1 'O'
#define MON_MAGIC_2 'N'

/* ── ATA PIO Disk Driver (store.img on IDE primary slave) ── */
/* store.img is -drive if=ide,index=1 → primary slave (0x1F0, drive=1) */
#define ATA_DATA    0x1F0
#define ATA_ERROR   0x1F1
#define ATA_COUNT   0x1F2
#define ATA_SECTOR  0x1F3
#define ATA_CYLOW   0x1F4
#define ATA_CYHIGH  0x1F5
#define ATA_DRIVE   0x1F6
#define ATA_STATUS  0x1F7
#define ATA_CMD     0x1F7

#define ATA_STATUS_BSY  0x80
#define ATA_STATUS_DRQ  0x08
#define ATA_STATUS_ERR  0x01
#define ATA_CMD_READ    0x20
#define ATA_CMD_WRITE   0x30

#define STORE_SECTOR_SIZE 512
#define STORE_DISK_SECTORS 8192  /* 4MB / 512B = 8192 sectors */

static int ata_store_ready = 0;

static void ata_wait_bsy(void) {
    for (int i = 0; i < 100000; i++) {
        if (!(inb(ATA_STATUS) & ATA_STATUS_BSY)) return;
    }
}

static void ata_wait_drq(void) {
    for (int i = 0; i < 100000; i++) {
        if (inb(ATA_STATUS) & ATA_STATUS_DRQ) return;
    }
}

/* Select primary slave drive */
static void ata_select_slave(void) {
    outb(ATA_DRIVE, 0xF0);  /* 0xF0 = slave, LBA mode */
    /* 400ns delay: read status 4 times */
    inb(ATA_STATUS); inb(ATA_STATUS); inb(ATA_STATUS); inb(ATA_STATUS);
}

/* Read one 512-byte sector from store disk */
int ata_read_sector(u32 lba, u8 *buf) {
    if (!ata_store_ready) return -1;
    if (lba >= STORE_DISK_SECTORS) return -1;

    ata_wait_bsy();
    outb(ATA_DRIVE, 0xF0 | ((lba >> 24) & 0x0F));  /* slave + LBA high bits */
    outb(ATA_COUNT, 1);
    outb(ATA_SECTOR, lba & 0xFF);
    outb(ATA_CYLOW, (lba >> 8) & 0xFF);
    outb(ATA_CYHIGH, (lba >> 16) & 0xFF);
    outb(ATA_CMD, ATA_CMD_READ);

    ata_wait_bsy();
    ata_wait_drq();

    if (inb(ATA_STATUS) & ATA_STATUS_ERR) return -1;

    /* Read 256 words (512 bytes) */
    u16 *p = (u16 *)buf;
    for (int i = 0; i < 256; i++) {
        p[i] = inw(ATA_DATA);
    }
    return 0;
}

/* Write one 512-byte sector to store disk */
int ata_write_sector(u32 lba, const u8 *buf) {
    if (!ata_store_ready) return -1;
    if (lba >= STORE_DISK_SECTORS) return -1;

    ata_wait_bsy();
    outb(ATA_DRIVE, 0xF0 | ((lba >> 24) & 0x0F));
    outb(ATA_COUNT, 1);
    outb(ATA_SECTOR, lba & 0xFF);
    outb(ATA_CYLOW, (lba >> 8) & 0xFF);
    outb(ATA_CYHIGH, (lba >> 16) & 0xFF);
    outb(ATA_CMD, ATA_CMD_WRITE);

    ata_wait_bsy();
    ata_wait_drq();

    /* Write 256 words (512 bytes) */
    const u16 *p = (const u16 *)buf;
    for (int i = 0; i < 256; i++) {
        outw(ATA_DATA, p[i]);
    }

    /* Flush */
    ata_wait_bsy();
    return (inb(ATA_STATUS) & ATA_STATUS_ERR) ? -1 : 0;
}

/* ── Context Store (persistent on disk) ── */
/* Sector 0: header, Sectors 1+: entries */
#define CTX_MAGIC 0x58455443  /* "CTEX" */
#define CTX_MAX_ENTRIES 500
#define CTX_ENTRY_SIZE 256    /* fixed-size entries */
/* 1 header sector + ceil(500*256/512) = 1 + 250 = 251 sectors */

typedef struct {
    u32 magic;
    u32 version;
    u32 entry_count;
    u32 write_index;       /* next write position (wraps) */
    u32 total_written;
    u32 reserved[3];
} __attribute__((packed)) ctx_header_t;

typedef struct {
    u32 timestamp;
    u8  type;              /* 1=command, 2=result, 3=fact, 4=event */
    u8  len;               /* data length (max 249) */
    u8  reserved[2];
    char data[248];        /* UTF-8 text, null-terminated */
} __attribute__((packed)) ctx_entry_t;

static ctx_header_t ctx_header;

void ctx_store_init(void) {
    serial_print("[CTX] Probing IDE slave...\n");

    /* Soft-probe: select slave and check for presence */
    outb(ATA_DRIVE, 0xB0);  /* select slave */
    /* 400ns delay */
    inb(0x3F6); inb(0x3F6); inb(0x3F6); inb(0x3F6);

    /* Write a pattern to sector count/number and read back */
    outb(ATA_COUNT, 0x55);
    outb(ATA_SECTOR, 0xAA);
    u8 cnt = inb(ATA_COUNT);
    u8 sec = inb(ATA_SECTOR);

    if (cnt != 0x55 || sec != 0xAA) {
        serial_print("[CTX] No store disk (probe failed)\n");
        vga_print("  [CTX] No store disk\n");
        ata_store_ready = 0;
        return;
    }
    ata_store_ready = 1;
    serial_print("[CTX] IDE slave detected\n");

    /* Read header sector */
    static u8 sector[512];
    if (ata_read_sector(0, sector) < 0) {
        serial_print("[CTX] Disk read error\n");
        vga_print("  [CTX] Disk read error\n");
        ata_store_ready = 0;
        return;
    }

    mem_copy(&ctx_header, sector, sizeof(ctx_header_t));

    if (ctx_header.magic != CTX_MAGIC) {
        /* First use — initialize header */
        serial_print("[CTX] Initializing store disk\n");
        mem_set(&ctx_header, 0, sizeof(ctx_header_t));
        ctx_header.magic = CTX_MAGIC;
        ctx_header.version = 1;

        mem_set(sector, 0, 512);
        mem_copy(sector, &ctx_header, sizeof(ctx_header_t));
        ata_write_sector(0, sector);
    }

    serial_print("[CTX] Store ready: ");
    serial_hex(ctx_header.entry_count);
    serial_print(" entries\n");

    vga_print("  [CTX] Context store: ");
    vga_print_dec(ctx_header.entry_count);
    vga_print(" entries on disk\n");
}

/* Append an entry to the context store */
int ctx_store_append(u8 type, u32 timestamp, const char *data, int data_len) {
    if (!ata_store_ready) return -1;
    if (data_len > 248) data_len = 248;

    /* Build entry */
    ctx_entry_t entry;
    mem_set(&entry, 0, sizeof(ctx_entry_t));
    entry.timestamp = timestamp;
    entry.type = type;
    entry.len = data_len;
    mem_copy(entry.data, (u8 *)data, data_len);

    /* Calculate sector and offset: 2 entries per sector (256*2=512) */
    u32 idx = ctx_header.write_index;
    u32 sector_num = 1 + (idx / 2);  /* sector 0 is header */
    u32 offset = (idx % 2) * 256;

    /* Read sector, modify entry, write back */
    u8 sector[512];
    ata_read_sector(sector_num, sector);
    mem_copy(sector + offset, &entry, sizeof(ctx_entry_t));
    ata_write_sector(sector_num, sector);

    /* Update header */
    ctx_header.write_index = (idx + 1) % CTX_MAX_ENTRIES;
    if (ctx_header.entry_count < CTX_MAX_ENTRIES) ctx_header.entry_count++;
    ctx_header.total_written++;

    /* Write header sector */
    u8 hdr_sector[512];
    mem_set(hdr_sector, 0, 512);
    mem_copy(hdr_sector, &ctx_header, sizeof(ctx_header_t));
    ata_write_sector(0, hdr_sector);

    return ctx_header.total_written - 1;
}

/* Read recent N entries (newest first) */
int ctx_store_read_recent(ctx_entry_t *out, int count) {
    if (!ata_store_ready || ctx_header.entry_count == 0) return 0;
    if (count > (int)ctx_header.entry_count) count = ctx_header.entry_count;

    int read = 0;
    int idx = (ctx_header.write_index - 1 + CTX_MAX_ENTRIES) % CTX_MAX_ENTRIES;

    for (int i = 0; i < count; i++) {
        u32 sector_num = 1 + (idx / 2);
        u32 offset = (idx % 2) * 256;

        u8 sector[512];
        ata_read_sector(sector_num, sector);
        mem_copy(&out[read], sector + offset, sizeof(ctx_entry_t));
        read++;

        idx = (idx - 1 + CTX_MAX_ENTRIES) % CTX_MAX_ENTRIES;
    }
    return read;
}

/* ── Virtual Registers (server room simulation) ── */
/* Address 0x200000 + index*4: each register is u32 LE */
static volatile u32 *virt_regs = (volatile u32 *)0x200000;

/* Server room registers */
#define VREG_TEMP       0   /* room temperature (°C) */
#define VREG_CPU_LOAD   1   /* server CPU load (0-100%) */
#define VREG_COOLING    2   /* cooling fan power (0=off, 1=low, 2=high) */
#define VREG_POWER      3   /* total power draw (watts) */
#define VREG_SRV_WEB    4   /* web server: 0=off, 1=on */
#define VREG_SRV_DB     5   /* database server: 0=off, 1=on */
#define VREG_SRV_CACHE  6   /* cache server: 0=off, 1=on */
#define VREG_SRV_LOG    7   /* log server: 0=off, 1=on */
#define VREG_SRV_BACKUP 8   /* backup server: 0=off, 1=on (lowest priority) */
#define VREG_SRV_DEV    9   /* dev server: 0=off, 1=on (lowest priority) */
#define VREG_ALERT      10  /* alert level: 0=normal, 1=warning, 2=critical */
#define VREG_UPTIME     11  /* system uptime ticks */
#define VREG_COUNT      16

void virt_regs_init(void) {
    for (int i = 0; i < VREG_COUNT; i++) virt_regs[i] = 0;
    virt_regs[VREG_TEMP] = 22;       /* room temp: 22°C */
    virt_regs[VREG_CPU_LOAD] = 40;   /* moderate load */
    virt_regs[VREG_COOLING] = 1;     /* cooling: low */
    virt_regs[VREG_POWER] = 800;     /* 800W baseline */
    virt_regs[VREG_SRV_WEB] = 1;     /* web: on (critical) */
    virt_regs[VREG_SRV_DB] = 1;      /* db: on (critical) */
    virt_regs[VREG_SRV_CACHE] = 1;   /* cache: on (important) */
    virt_regs[VREG_SRV_LOG] = 1;     /* log: on (normal) */
    virt_regs[VREG_SRV_BACKUP] = 1;  /* backup: on (low priority) */
    virt_regs[VREG_SRV_DEV] = 1;     /* dev: on (low priority) */
    virt_regs[VREG_ALERT] = 0;       /* normal */
}

/* Simulate server room dynamics */
void virt_regs_tick(void) {
    static u32 tick_count = 0;
    tick_count++;
    if (tick_count % 500000 == 0) {
        u32 temp = virt_regs[VREG_TEMP];
        u32 cpu = virt_regs[VREG_CPU_LOAD];
        u32 cooling = virt_regs[VREG_COOLING];

        /* Count active servers → affects CPU load and heat */
        u32 active_servers = 0;
        for (int i = VREG_SRV_WEB; i <= VREG_SRV_DEV; i++)
            if (virt_regs[i]) active_servers++;

        /* CPU load increases with active servers */
        u32 target_cpu = active_servers * 15 + 10;  /* 10% base + 15% per server */
        if (cpu < target_cpu) cpu += 2;
        else if (cpu > target_cpu) cpu -= 2;
        if (cpu > 100) cpu = 100;
        virt_regs[VREG_CPU_LOAD] = cpu;

        /* Temperature: rises with CPU, drops with cooling */
        u32 heat_rate = cpu / 25;  /* 0-4 degrees per tick from CPU */
        u32 cool_rate = cooling;   /* 0-2 degrees per tick from cooling */
        if (heat_rate > cool_rate) {
            if (temp < 80) temp += (heat_rate - cool_rate);
        } else {
            if (temp > 18) temp -= (cool_rate - heat_rate);
        }
        virt_regs[VREG_TEMP] = temp;

        /* Power = base + per-server + cooling */
        u32 power = 200;  /* base */
        power += active_servers * 120;  /* 120W per server */
        power += cooling * 100;  /* 100W per cooling level */
        virt_regs[VREG_POWER] = power;

        /* Auto-alert based on temp */
        if (temp >= 45) virt_regs[VREG_ALERT] = 2;       /* critical */
        else if (temp >= 35) virt_regs[VREG_ALERT] = 1;  /* warning */
        else virt_regs[VREG_ALERT] = 0;                   /* normal */

        /* Uptime */
        virt_regs[VREG_UPTIME]++;
    }
}

void monitor_init(void) {
    for (int i = 0; i < MAX_MONITORS; i++) {
        monitors[i].active = 0;
        monitors[i].trigger_count = 0;
    }
    pending_event = -1;
    pending_event_value = 0;
}

int monitor_register(u8 *code, u32 code_len, u32 interval,
                     u8 cond_op, u32 cond_val, u32 hub_ip, u16 hub_port) {
    for (int i = 0; i < MAX_MONITORS; i++) {
        if (!monitors[i].active) {
            if (code_len > MONITOR_CODE_SIZE) code_len = MONITOR_CODE_SIZE;
            mem_copy(monitors[i].code, code, code_len);
            monitors[i].code_len = code_len;
            monitors[i].interval_ticks = interval;
            monitors[i].ticks_remaining = interval;
            monitors[i].condition_op = cond_op;
            monitors[i].condition_val = cond_val;
            monitors[i].hub_ip_addr = hub_ip;
            monitors[i].hub_port = hub_port;
            monitors[i].trigger_count = 0;
            monitors[i].last_value = 0;
            monitors[i].fired = 0;
            monitors[i].active = 1;
            serial_print("[MON] registered monitor ");
            serial_hex(i);
            serial_print("\n");
            return i;
        }
    }
    return -1;  /* no slot available */
}

void monitor_tick(void) {
    /* Called from main loop polling cycle */
    monitor_poll_counter++;

    for (int i = 0; i < MAX_MONITORS; i++) {
        monitor_t *m = &monitors[i];
        if (!m->active) continue;

        m->ticks_remaining--;
        if (m->ticks_remaining > 0) continue;
        m->ticks_remaining = m->interval_ticks;

        /* Execute monitor code — returns value in EAX */
        u32 (*mon_fn)(void) = (u32 (*)(void))m->code;
        u32 val = mon_fn();
        m->last_value = val;

        /* Check condition */
        int triggered = 0;
        switch (m->condition_op) {
            case MON_OP_GT: triggered = (val > m->condition_val); break;
            case MON_OP_LT: triggered = (val < m->condition_val); break;
            case MON_OP_EQ: triggered = (val == m->condition_val); break;
            case MON_OP_NE: triggered = (val != m->condition_val); break;
        }

        if (triggered && !m->fired) {
            m->trigger_count++;
            m->fired = 1;  /* prevent rapid re-fire */
            pending_event = i;
            pending_event_value = val;
            serial_print("[MON] TRIGGERED monitor ");
            serial_hex(i);
            serial_print(" val=");
            serial_hex(val);
            serial_print("\n");
        } else if (!triggered) {
            m->fired = 0;  /* reset when condition clears */
        }
    }
}

/* ── TCP Client: fire event to hub ── */
/* Uses a separate seq/state from the server connection */
static u32 client_seq = 10000;
static int client_state = 0;  /* 0=idle, 1=syn_sent, 2=connected, 3=done */

void fire_event_to_hub(int mon_idx, u32 value) {
    monitor_t *m = &monitors[mon_idx];
    u8 hub_ip[4];
    hub_ip[0] = (m->hub_ip_addr) & 0xFF;
    hub_ip[1] = (m->hub_ip_addr >> 8) & 0xFF;
    hub_ip[2] = (m->hub_ip_addr >> 16) & 0xFF;
    hub_ip[3] = (m->hub_ip_addr >> 24) & 0xFF;

    /* For QEMU: hub is at gateway (10.0.2.2), use gateway MAC */
    /* We need to ARP for the hub IP first if not cached */
    if (!hub_mac_resolved) {
        /* Send ARP request for hub IP */
        u8 arp[60]; mem_set(arp, 0, 60);
        mem_set(arp, 0xFF, 6);  /* broadcast */
        mem_copy(arp + 6, my_mac, 6);
        arp[12] = 0x08; arp[13] = 0x06;  /* ARP */
        arp[14] = 0x00; arp[15] = 0x01;  /* HW = ethernet */
        arp[16] = 0x08; arp[17] = 0x00;  /* proto = IPv4 */
        arp[18] = 6; arp[19] = 4;
        arp[20] = 0x00; arp[21] = 0x01;  /* REQUEST */
        mem_copy(arp + 22, my_mac, 6);
        mem_copy(arp + 28, my_ip, 4);
        mem_set(arp + 32, 0, 6);
        mem_copy(arp + 38, hub_ip, 4);
        nic_send(arp, 60);
        serial_print("[MON] ARP request for hub\n");
        /* MAC will be captured when ARP reply comes in handle_packet */
        return;  /* retry next cycle */
    }

    /* Build HTTP POST /event */
    u8 body[256]; int blen = 0;
    const char *bp;

    bp = "{\"edge\":\"x86-qemu\",\"monitor\":";
    while (*bp) body[blen++] = *bp++;
    body[blen++] = '0' + mon_idx;
    bp = ",\"value\":";
    while (*bp) body[blen++] = *bp++;
    /* decimal value */
    char vbuf[12]; int vi = 0;
    u32 v = value;
    if (v == 0) { vbuf[vi++] = '0'; }
    else { while (v > 0) { vbuf[vi++] = '0' + (v % 10); v /= 10; } }
    while (vi > 0) body[blen++] = vbuf[--vi];
    bp = ",\"trigger\":\"";
    while (*bp) body[blen++] = *bp++;
    switch (m->condition_op) {
        case MON_OP_GT: bp = "gt"; break;
        case MON_OP_LT: bp = "lt"; break;
        case MON_OP_EQ: bp = "eq"; break;
        case MON_OP_NE: bp = "ne"; break;
        default: bp = "?"; break;
    }
    while (*bp) body[blen++] = *bp++;
    body[blen++] = ':';
    vi = 0; v = m->condition_val;
    if (v == 0) { vbuf[vi++] = '0'; }
    else { while (v > 0) { vbuf[vi++] = '0' + (v % 10); v /= 10; } }
    while (vi > 0) body[blen++] = vbuf[--vi];
    bp = "\"}";
    while (*bp) body[blen++] = *bp++;

    /* Build full HTTP request */
    u8 req[512]; int rlen = 0;
    bp = "POST /event HTTP/1.1\r\nHost: hub\r\nContent-Type: application/json\r\nContent-Length: ";
    while (*bp) req[rlen++] = *bp++;
    /* content length decimal */
    vi = 0; v = blen;
    if (v == 0) { vbuf[vi++] = '0'; }
    else { while (v > 0) { vbuf[vi++] = '0' + (v % 10); v /= 10; } }
    while (vi > 0) req[rlen++] = vbuf[--vi];
    bp = "\r\nConnection: close\r\n\r\n";
    while (*bp) req[rlen++] = *bp++;
    mem_copy(req + rlen, body, blen);
    rlen += blen;

    /* Send via TCP — reuse existing tcp_send but with hub as destination
     * For simplicity: send as a single TCP PSH+ACK+FIN packet
     * The hub will receive this as a new connection data */
    u16 hub_port_net = ((m->hub_port & 0xFF) << 8) | ((m->hub_port >> 8) & 0xFF);

    /* Save/restore server connection state */
    u32 save_local_seq = tcp_local_seq;
    u32 save_remote_seq = tcp_remote_seq;

    tcp_local_seq = client_seq;
    tcp_remote_seq = 0;

    /* SYN */
    tcp_send(hub_mac, hub_ip, hub_port_net, TCP_SYN, 0, 0);
    client_seq = tcp_local_seq;

    /* Restore server state */
    tcp_local_seq = save_local_seq;
    tcp_remote_seq = save_remote_seq;

    /* The rest of the handshake will be handled when we receive SYN-ACK
     * via handle_tcp — we need to detect client-mode packets */
    client_state = 1;  /* syn_sent */

    serial_print("[MON] SYN sent to hub\n");
}

/* Resident processes: not needed in kernel.
 * LLM generates code with loops/delays/conditions.
 * Hub re-deploys as needed. Kernel stays simple. */

/* ── BochsVBE Graphics ── */
#define VBE_INDEX 0x01CE
#define VBE_DATA  0x01CF

static u32 fb_addr = 0;
static int gfx_mode = 0;
static int gfx_width = 0;
static int gfx_height = 0;
static int gfx_pitch = 0; /* bytes per scanline */

/* Find VGA device's framebuffer via PCI BAR0 */
u32 find_vga_fb(void) {
    for (int bus = 0; bus < 256; bus++) {
        for (int slot = 0; slot < 32; slot++) {
            u32 id = pci_read(bus, slot, 0, 0x08);
            u8 class = (id >> 24) & 0xFF;
            u8 subclass = (id >> 16) & 0xFF;
            /* VGA compatible controller: class 0x03, subclass 0x00 */
            if (class == 0x03 && subclass == 0x00) {
                u32 bar0 = pci_read(bus, slot, 0, 0x10);
                u32 addr = bar0 & 0xFFFFFFF0;
                serial_print("[VGA] Found at PCI ");
                serial_hex(bus); serial_putc(':'); serial_hex(slot);
                serial_print(" BAR0="); serial_hex(addr);
                serial_print("\n");
                return addr;
            }
        }
    }
    serial_print("[VGA] Not found, using default\n");
    return 0xFD000000;
}

void vbe_set_mode(int w, int h) {
    if (fb_addr == 0) fb_addr = find_vga_fb();

    outw(VBE_INDEX, 0x04); outw(VBE_DATA, 0x00);       /* disable */
    outw(VBE_INDEX, 0x01); outw(VBE_DATA, (u16)w);      /* xres */
    outw(VBE_INDEX, 0x02); outw(VBE_DATA, (u16)h);      /* yres */
    outw(VBE_INDEX, 0x03); outw(VBE_DATA, 32);           /* 32bpp */
    outw(VBE_INDEX, 0x04); outw(VBE_DATA, 0x41);         /* enable + LFB */

    /* Read back virtual width (pitch) */
    outw(VBE_INDEX, 0x06);
    u16 virt_w = inw(VBE_DATA);

    gfx_mode = 1;
    gfx_width = w;
    gfx_height = h;
    gfx_pitch = (virt_w ? virt_w : w) * 4; /* bytes per line */

    serial_print("[VGA] Mode set: ");
    serial_hex(w); serial_putc('x'); serial_hex(h);
    serial_print(" fb="); serial_hex(fb_addr);
    serial_print(" virt_w="); serial_hex(virt_w);
    serial_print(" pitch="); serial_hex(gfx_pitch);
    serial_print("\n");
}

void fb_pixel(int x, int y, u8 r, u8 g, u8 b) {
    if (x < 0 || x >= gfx_width || y < 0 || y >= gfx_height) return;
    /* Use pitch (bytes per line) for correct stride */
    u8 *fb = (u8 *)fb_addr;
    u32 *pixel = (u32 *)(fb + y * gfx_pitch + x * 4);
    *pixel = (r << 16) | (g << 8) | b;
}

void fb_clear(u8 r, u8 g, u8 b) {
    u8 *fb = (u8 *)fb_addr;
    u32 color = (r << 16) | (g << 8) | b;
    for (int y = 0; y < gfx_height; y++) {
        u32 *row = (u32 *)(fb + y * gfx_pitch);
        for (int x = 0; x < gfx_width; x++)
            row[x] = color;
    }
}

void fb_fill_circle(int cx, int cy, int radius, u8 r, u8 g, u8 b) {
    for (int y = cy - radius; y <= cy + radius; y++) {
        for (int x = cx - radius; x <= cx + radius; x++) {
            int dx = x - cx, dy = y - cy;
            if (dx*dx + dy*dy <= radius*radius)
                fb_pixel(x, y, r, g, b);
        }
    }
}

void fb_fill_ellipse(int cx, int cy, int rx, int ry, u8 r, u8 g, u8 b) {
    for (int y = cy - ry; y <= cy + ry; y++) {
        for (int x = cx - rx; x <= cx + rx; x++) {
            int dx = x - cx, dy = y - cy;
            if ((dx*dx*ry*ry + dy*dy*rx*rx) <= rx*rx*ry*ry)
                fb_pixel(x, y, r, g, b);
        }
    }
}

/* Result buffer — injected code writes here, HTTP returns it */
#define RESULT_BUF_SIZE 256
static char result_buf[RESULT_BUF_SIZE];
static int result_len = 0;

u16 net_checksum(u8 *data, int len) {
    u32 sum = 0;
    u16 *p = (u16 *)data;
    while (len > 1) { sum += *p++; len -= 2; }
    if (len == 1) sum += *(u8 *)p;
    while (sum >> 16) sum = (sum & 0xFFFF) + (sum >> 16);
    return ~sum;
}

u16 tcp_checksum(u8 *tcp_start, int tcp_len, u8 *src_ip, u8 *dst_ip) {
    u32 sum = 0;

    /* Pseudo header — read IPs as native u16 (same as packet bytes) */
    u16 *sp = (u16 *)src_ip;
    u16 *dp = (u16 *)dst_ip;
    sum += sp[0]; sum += sp[1];
    sum += dp[0]; sum += dp[1];

    /* Protocol and TCP length in network byte order */
    u8 pseudo_tail[4];
    pseudo_tail[0] = 0;
    pseudo_tail[1] = 6;  /* TCP */
    pseudo_tail[2] = (tcp_len >> 8) & 0xFF;
    pseudo_tail[3] = tcp_len & 0xFF;
    sum += *(u16 *)&pseudo_tail[0];
    sum += *(u16 *)&pseudo_tail[2];

    /* TCP header + data */
    u16 *p = (u16 *)tcp_start;
    int remaining = tcp_len;
    while (remaining > 1) { sum += *p++; remaining -= 2; }
    if (remaining == 1) sum += *(u8 *)p;

    while (sum >> 16) sum = (sum & 0xFFFF) + (sum >> 16);
    return (u16)(~sum);
}

void tcp_send(u8 *dst_mac, u8 *dst_ip, u16 dst_port, u8 flags, u8 *data, int data_len) {
    u8 pkt[1500];
    mem_set(pkt, 0, 1500);
    int offset = 0;

    /* Ethernet */
    struct eth_header *eth = (struct eth_header *)pkt;
    mem_copy(eth->dst, dst_mac, 6);
    mem_copy(eth->src, my_mac, 6);
    eth->type = ETH_IP;
    offset = 14;

    /* IP */
    struct ip_header *ip = (struct ip_header *)(pkt + offset);
    int tcp_hdr_len = 20;
    int ip_total = 20 + tcp_hdr_len + data_len;

    ip->ver_ihl = 0x45;
    ip->tos = 0;
    ip->total_len = ((ip_total & 0xFF) << 8) | ((ip_total >> 8) & 0xFF);
    ip->id = 0;
    ip->flags_frag = 0;
    ip->ttl = 64;
    ip->protocol = 6; /* TCP */
    ip->checksum = 0;
    mem_copy(ip->src, my_ip, 4);
    mem_copy(ip->dst, dst_ip, 4);
    ip->checksum = ip_checksum(ip, 20);
    offset += 20;

    /* TCP */
    struct tcp_header *tcp = (struct tcp_header *)(pkt + offset);
    tcp->src_port = ((tcp_local_port & 0xFF) << 8) | ((tcp_local_port >> 8) & 0xFF);
    tcp->dst_port = dst_port;
    tcp->seq = __builtin_bswap32(tcp_local_seq);
    tcp->ack = __builtin_bswap32(tcp_remote_seq);
    tcp->data_offset = (5 << 4);
    tcp->flags = flags;
    tcp->window = 0x00FF; /* 65280 in network order... let's use smaller */
    tcp->checksum = 0;
    tcp->urgent = 0;
    offset += tcp_hdr_len;

    /* Data */
    if (data_len > 0) {
        mem_copy(pkt + offset, data, data_len);
    }

    /* TCP checksum — over TCP header + data */
    int tcp_total = tcp_hdr_len + data_len;
    tcp->checksum = 0;
    u16 cksum = tcp_checksum((u8 *)tcp, tcp_total, my_ip, dst_ip);
    /* Store in network byte order */
    tcp->checksum = cksum;

    nic_send(pkt, offset + data_len);

    if (flags & TCP_SYN) tcp_local_seq++;
    if (flags & TCP_FIN) tcp_local_seq++;
    if (data_len > 0) tcp_local_seq += data_len;
}

/* ── HTTP ── */
static u8 remote_mac[6];
static u8 remote_ip[4];
static u16 remote_port;

const char http_200[] = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n";
const char http_ok[] = "OK: code executed\n";

void handle_http(void) {
    /* Find POST /poke */
    int is_post = 0;
    int body_start = -1;

    for (int i = 0; i < http_buf_len - 3; i++) {
        if (http_buf[i] == 'P' && http_buf[i+1] == 'O' && http_buf[i+2] == 'S' && http_buf[i+3] == 'T')
            is_post = 1;
        if (http_buf[i] == '\r' && http_buf[i+1] == '\n' && http_buf[i+2] == '\r' && http_buf[i+3] == '\n') {
            body_start = i + 4;
            break;
        }
    }

    /* Check for stream protocol: body starts with "STR" */
    if (is_post && body_start >= 0 && (http_buf_len - body_start) >= 3) {
        u8 *bd = http_buf + body_start;
        if (bd[0] == 'S' && bd[1] == 'T' && bd[2] == 'R') {
            serial_print("[STREAM] entering stream mode\n");
            if (!gfx_mode) vbe_set_mode(640, 480);

            /* Send OK immediately, then keep connection open */
            u8 resp[128]; mem_set(resp, 0, 128);
            int rl = 0;
            const char *rp = "HTTP/1.1 200 OK\r\nConnection: keep-alive\r\n\r\nSTREAM OK\n";
            while (*rp) resp[rl++] = *rp++;
            tcp_send(remote_mac, remote_ip, remote_port, TCP_ACK | TCP_PSH, resp, rl);

            /* Enter stream receive loop */
            /* Remaining data after "STR" might have first frame */
            u8 *stream_ptr = bd + 3;
            int stream_remain = http_buf_len - body_start - 3;

            /* Stream state */
            static u8 stream_buf[25000]; /* 80*80*3=19200 + header + margin */
            int sbuf_len = 0;

            /* Copy any remaining data */
            if (stream_remain > 0) {
                mem_copy(stream_buf, stream_ptr, stream_remain);
                sbuf_len = stream_remain;
            }

            /* Stream loop — keep receiving and rendering frames */
            u8 recv_buf2[2048];
            int stream_active = 1;
            int frame_count = 0;

            while (stream_active) {
                /* Check for FRM header in buffer */
                while (sbuf_len >= 11) { /* FRM(3) + W(2) + H(2) + size(4) = 11 */
                    if (stream_buf[0] == 'F' && stream_buf[1] == 'R' && stream_buf[2] == 'M') {
                        u16 fw = stream_buf[3] | (stream_buf[4] << 8);
                        u16 fh = stream_buf[5] | (stream_buf[6] << 8);
                        u32 fsize = stream_buf[7] | (stream_buf[8] << 8) |
                                   (stream_buf[9] << 16) | (stream_buf[10] << 24);

                        if (sbuf_len >= (int)(11 + fsize)) {
                            /* Full frame available — render it */
                            u8 *fpixels = stream_buf + 11;
                            int fox = (gfx_width - fw) / 2;
                            int foy = (gfx_height - fh) / 2;

                            for (int py = 0; py < fh; py++) {
                                for (int px = 0; px < fw; px++) {
                                    int pi = (py * fw + px) * 3;
                                    if (pi + 2 < (int)fsize)
                                        fb_pixel(fox + px, foy + py,
                                                fpixels[pi], fpixels[pi+1], fpixels[pi+2]);
                                }
                            }
                            frame_count++;
                            serial_print("[FRM] #");
                            serial_hex(frame_count);
                            serial_print(" w="); serial_hex(fw);
                            serial_print(" h="); serial_hex(fh);
                            serial_print(" sz="); serial_hex(fsize);
                            serial_print(" buf="); serial_hex(sbuf_len);
                            serial_print("\n");

                            /* Shift buffer */
                            int consumed = 11 + fsize;
                            int left = sbuf_len - consumed;
                            if (left > 0) {
                                for (int i = 0; i < left; i++)
                                    stream_buf[i] = stream_buf[consumed + i];
                            }
                            sbuf_len = left;
                        } else {
                            break; /* Need more data */
                        }
                    } else if (stream_buf[0] == 'E' && stream_buf[1] == 'N' && stream_buf[2] == 'D') {
                        stream_active = 0;
                        break;
                    } else {
                        /* Unknown byte, skip */
                        for (int i = 0; i < sbuf_len - 1; i++)
                            stream_buf[i] = stream_buf[i + 1];
                        sbuf_len--;
                    }
                }

                /* Receive more TCP data */
                int rlen = nic_recv(recv_buf2);
                if (rlen > 0) {
                    struct eth_header *re = (struct eth_header *)recv_buf2;
                    if (re->type == ETH_ARP) {
                        handle_arp(recv_buf2, rlen);
                    } else if (re->type == ETH_IP) {
                        struct ip_header *ri = (struct ip_header *)(recv_buf2 + 14);
                        if (ri->protocol == 6) {
                            struct tcp_header *rt = (struct tcp_header *)(recv_buf2 + 34);
                            int rt_hdr = (rt->data_offset >> 4) * 4;
                            int rt_iplen = ((ri->total_len & 0xFF) << 8) | ((ri->total_len >> 8) & 0xFF);
                            int rt_datalen = rt_iplen - 20 - rt_hdr;

                            if (rt->flags & TCP_FIN) {
                                stream_active = 0;
                                tcp_remote_seq = __builtin_bswap32(rt->seq) + rt_datalen + 1;
                                tcp_send(remote_mac, remote_ip, remote_port, TCP_ACK | TCP_FIN, 0, 0);
                            } else if (rt_datalen > 0) {
                                u8 *rt_data = recv_buf2 + 34 + rt_hdr;
                                tcp_remote_seq = __builtin_bswap32(rt->seq) + rt_datalen;
                                tcp_send(remote_mac, remote_ip, remote_port, TCP_ACK, 0, 0);

                                /* Append to stream buffer — drop old data if full */
                                if (sbuf_len + rt_datalen >= (int)sizeof(stream_buf)) {
                                    /* Buffer full — skip to latest data */
                                    sbuf_len = 0;
                                }
                                mem_copy(stream_buf + sbuf_len, rt_data, rt_datalen);
                                sbuf_len += rt_datalen;
                            }
                        }
                    }
                }
            }

            serial_print("[STREAM] ended. frames=");
            serial_hex(frame_count);
            serial_print("\n");

            http_buf_len = 0;
            return;
        }
    }

    /* Check for image protocol: body starts with "IMG" */
    if (is_post && body_start >= 0 && (http_buf_len - body_start) >= 7) {
        u8 *bd = http_buf + body_start;
        if (bd[0] == 'I' && bd[1] == 'M' && bd[2] == 'G') {
            u16 img_w = bd[3] | (bd[4] << 8);
            u16 img_h = bd[5] | (bd[6] << 8);
            u8 *pixels = bd + 7;
            int pixel_count = img_w * img_h;
            int data_available = http_buf_len - body_start - 7;

            serial_print("[POKE] IMG received: ");
            serial_hex(img_w); serial_print("x"); serial_hex(img_h);
            serial_print("\n");

            /* Switch to graphics mode */
            if (!gfx_mode) vbe_set_mode(640, 480);
            fb_clear(30, 30, 50); /* dark blue-gray, not pure black */

            /* Debug: directly write to framebuffer instead of using fb_pixel */
            serial_print("[IMG] w="); serial_hex(img_w);
            serial_print(" h="); serial_hex(img_h);
            serial_print(" data_avail="); serial_hex(data_available);
            serial_print(" pixels_ptr="); serial_hex((u32)pixels);
            serial_print(" first3=");
            serial_hex(pixels[0]); serial_putc(',');
            serial_hex(pixels[1]); serial_putc(',');
            serial_hex(pixels[2]);
            serial_print("\n");

            int ox = (gfx_width - img_w) / 2;
            int oy = (gfx_height - img_h) / 2;

            /* Draw using pitch-aware fb_pixel */
            int drawn = 0;
            for (int py = 0; py < (int)img_h; py++) {
                for (int px = 0; px < (int)img_w; px++) {
                    int pi = (py * img_w + px) * 3;
                    if (pi + 2 >= data_available) break;
                    fb_pixel(ox + px, oy + py, pixels[pi], pixels[pi+1], pixels[pi+2]);
                    drawn++;
                }
            }
            serial_print("[IMG] drawn="); serial_hex(drawn); serial_print("\n");

            /* Send response */
            u8 resp[128]; mem_set(resp, 0, 128);
            int rl = 0;
            const char *rp = "HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nIMG OK\n";
            while (*rp) resp[rl++] = *rp++;
            tcp_send(remote_mac, remote_ip, remote_port, TCP_ACK | TCP_PSH | TCP_FIN, resp, rl);
            serial_print("[POKE] IMG drawn\n");
            return;
        }
    }

    /* Check for DRAW command: body starts with "DRAW" → procedural drawing */
    if (is_post && body_start >= 0 && (http_buf_len - body_start) >= 4) {
        u8 *bd = http_buf + body_start;
        if (bd[0] == 'D' && bd[1] == 'R' && bd[2] == 'A' && bd[3] == 'W') {
            serial_print("[POKE] DRAW command\n");

            if (!gfx_mode) vbe_set_mode(640, 480);
            fb_clear(20, 20, 30); /* dark bg */

            /* Parse simple draw commands after "DRAW" */
            /* For now: just execute the code that follows as machine code */
            u8 *code = bd + 4;
            int code_len = http_buf_len - body_start - 4;
            if (code_len > 0 && code_len < CODE_BUF_SIZE) {
                mem_copy(code_buf, code, code_len);
                /* Pass framebuffer functions via known addresses */
                /* The code can call our fb_pixel etc via function pointers */
                void (*fn)(void) = (void (*)(void))code_buf;
                fn();
            }

            u8 resp[128]; mem_set(resp, 0, 128);
            int rl = 0;
            const char *rp = "HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nDRAW OK\n";
            while (*rp) resp[rl++] = *rp++;
            tcp_send(remote_mac, remote_ip, remote_port, TCP_ACK | TCP_PSH | TCP_FIN, resp, rl);
            return;
        }
    }

    serial_print("[POKE] HTTP request received, is_post=");
    serial_putc(is_post ? '1' : '0');
    serial_print(" body_start=");
    serial_hex(body_start);
    serial_print("\n");

    /* Check for GET /key — return last scancode */
    for (int i = 0; i < http_buf_len - 3; i++) {
        if (http_buf[i]=='/' && http_buf[i+1]=='k' && http_buf[i+2]=='e' && http_buf[i+3]=='y') {
            volatile u32 wi = kb_write_idx;  /* force re-read from memory */
            u8 last = (wi > 0) ? kb_ring[(wi - 1) % KB_BUF_SIZE] : 0;
            u8 kr[80]; int kl = 0;
            const char *kh = "HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n";
            while (*kh) kr[kl++] = *kh++;
            kh = "key="; while (*kh) kr[kl++] = *kh++;
            if (last == 0) { kr[kl++] = '0'; }
            else { char d[4]; int di=0; u32 lv=last; while(lv>0){d[di++]='0'+lv%10;lv/=10;} while(di>0)kr[kl++]=d[--di]; }
            kr[kl++] = '\n';
            tcp_send(remote_mac, remote_ip, remote_port, TCP_ACK | TCP_PSH | TCP_FIN, kr, kl);
            return;
        }
    }

    /* Check for GET /health */
    int is_health = 0;
    for (int i = 0; i < http_buf_len - 6; i++) {
        if (http_buf[i]=='/' && http_buf[i+1]=='h' && http_buf[i+2]=='e' &&
            http_buf[i+3]=='a' && http_buf[i+4]=='l' && http_buf[i+5]=='t' && http_buf[i+6]=='h') {
            is_health = 1; break;
        }
    }

    if (is_health) {
        /* Collect runtime stats */
        static u32 uptime_ticks = 0;
        static u32 exec_count = 0;
        static u32 total_bytes_recv = 0;
        uptime_ticks++;
        total_bytes_recv += http_buf_len;

        static u8 resp[512]; mem_set(resp, 0, 512);
        int rlen = 0;
        const char *h = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{";
        while (*h) resp[rlen++] = *h++;

        /* status */
        h = "\"status\":\"alive\",";
        while (*h) resp[rlen++] = *h++;

        /* arch */
        h = "\"arch\":\"i386\",";
        while (*h) resp[rlen++] = *h++;

        /* memory */
        h = "\"memory_mb\":64,";
        while (*h) resp[rlen++] = *h++;

        /* graphics mode */
        h = gfx_mode ? "\"gfx\":true," : "\"gfx\":false,";
        while (*h) resp[rlen++] = *h++;

        /* health requests (as proxy for uptime) */
        h = "\"health_pings\":";
        while (*h) resp[rlen++] = *h++;
        /* decimal */
        char nb[12]; int ni = 0; u32 v = uptime_ticks;
        if (v == 0) nb[ni++] = '0';
        else { while (v > 0) { nb[ni++] = '0' + v % 10; v /= 10; } }
        while (ni > 0) resp[rlen++] = nb[--ni];

        h = ",\"cpu_busy\":false";
        while (*h) resp[rlen++] = *h++;

        /* Monitor status */
        h = ",\"monitors\":[";
        while (*h) resp[rlen++] = *h++;
        int first_mon = 1;
        for (int mi = 0; mi < MAX_MONITORS; mi++) {
            if (!monitors[mi].active) continue;
            if (!first_mon) resp[rlen++] = ',';
            first_mon = 0;
            resp[rlen++] = '{';
            h = "\"id\":"; while (*h) resp[rlen++] = *h++;
            resp[rlen++] = '0' + mi;
            h = ",\"val\":"; while (*h) resp[rlen++] = *h++;
            ni = 0; v = monitors[mi].last_value;
            if (v == 0) nb[ni++] = '0';
            else { while (v > 0) { nb[ni++] = '0' + v % 10; v /= 10; } }
            while (ni > 0) resp[rlen++] = nb[--ni];
            h = ",\"fired\":"; while (*h) resp[rlen++] = *h++;
            resp[rlen++] = monitors[mi].fired ? '1' : '0';
            h = ",\"triggers\":"; while (*h) resp[rlen++] = *h++;
            ni = 0; v = monitors[mi].trigger_count;
            if (v == 0) nb[ni++] = '0';
            else { while (v > 0) { nb[ni++] = '0' + v % 10; v /= 10; } }
            while (ni > 0) resp[rlen++] = nb[--ni];
            resp[rlen++] = '}';
        }
        resp[rlen++] = ']';

        /* Virtual registers — server room (compact: key numbers) */
        /* t=temp c=cpu co=cooling p=power s=servers(bitmask) a=alert */
        h = ",\"vr\":{\"t\":"; while (*h) resp[rlen++] = *h++;
        ni=0; v=virt_regs[VREG_TEMP]; if(v==0)nb[ni++]='0'; else{while(v>0){nb[ni++]='0'+v%10;v/=10;}} while(ni>0)resp[rlen++]=nb[--ni];
        h = ",\"c\":"; while (*h) resp[rlen++] = *h++;
        ni=0; v=virt_regs[VREG_CPU_LOAD]; if(v==0)nb[ni++]='0'; else{while(v>0){nb[ni++]='0'+v%10;v/=10;}} while(ni>0)resp[rlen++]=nb[--ni];
        h = ",\"co\":"; while (*h) resp[rlen++] = *h++;
        resp[rlen++] = '0' + virt_regs[VREG_COOLING];
        h = ",\"p\":"; while (*h) resp[rlen++] = *h++;
        ni=0; v=virt_regs[VREG_POWER]; if(v==0)nb[ni++]='0'; else{while(v>0){nb[ni++]='0'+v%10;v/=10;}} while(ni>0)resp[rlen++]=nb[--ni];
        /* servers as bitmask: bit0=web,1=db,2=cache,3=log,4=backup,5=dev */
        h = ",\"srv\":"; while (*h) resp[rlen++] = *h++;
        v = (virt_regs[VREG_SRV_WEB]?1:0)|(virt_regs[VREG_SRV_DB]?2:0)|
            (virt_regs[VREG_SRV_CACHE]?4:0)|(virt_regs[VREG_SRV_LOG]?8:0)|
            (virt_regs[VREG_SRV_BACKUP]?16:0)|(virt_regs[VREG_SRV_DEV]?32:0);
        ni=0; if(v==0)nb[ni++]='0'; else{while(v>0){nb[ni++]='0'+v%10;v/=10;}} while(ni>0)resp[rlen++]=nb[--ni];
        h = ",\"a\":"; while (*h) resp[rlen++] = *h++;
        resp[rlen++] = '0' + virt_regs[VREG_ALERT];
        resp[rlen++] = '}';

        /* Context store info */
        h = ",\"ctx\":{\"ready\":"; while (*h) resp[rlen++] = *h++;
        resp[rlen++] = ata_store_ready ? '1' : '0';
        h = ",\"entries\":"; while (*h) resp[rlen++] = *h++;
        ni=0; v=ctx_header.entry_count; if(v==0)nb[ni++]='0'; else{while(v>0){nb[ni++]='0'+v%10;v/=10;}} while(ni>0)resp[rlen++]=nb[--ni];
        h = ",\"total\":"; while (*h) resp[rlen++] = *h++;
        ni=0; v=ctx_header.total_written; if(v==0)nb[ni++]='0'; else{while(v>0){nb[ni++]='0'+v%10;v/=10;}} while(ni>0)resp[rlen++]=nb[--ni];
        resp[rlen++] = '}';

        resp[rlen++] = '}';
        resp[rlen++] = '\n';

        tcp_send(remote_mac, remote_ip, remote_port, TCP_ACK | TCP_PSH | TCP_FIN, resp, rlen);
        return;
    }

    if (!is_post || body_start < 0) {
        u8 resp[256]; mem_set(resp, 0, 256);
        int rlen = 0;
        const char *h = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nPOKE OS ready\n";
        while (*h) resp[rlen++] = *h++;
        tcp_send(remote_mac, remote_ip, remote_port, TCP_ACK | TCP_PSH | TCP_FIN, resp, rlen);
        serial_print("[POKE] Sent ready response\n");
        return;
    }

    /* Check for DIE magic — graceful shutdown */
    int code_len = http_buf_len - body_start;
    u8 *body_ptr = http_buf + body_start;

    if (code_len >= 3 && body_ptr[0] == 'D' && body_ptr[1] == 'I' && body_ptr[2] == 'E') {
        serial_print("[POKE] SHUTDOWN requested\n");
        u8 resp[128]; int rl = 0;
        const char *rp = "HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nshutdown=ok\n";
        while (*rp) resp[rl++] = *rp++;
        tcp_send(remote_mac, remote_ip, remote_port, TCP_ACK | TCP_PSH | TCP_FIN, resp, rl);
        /* Give time for TCP to send */
        for (volatile int i = 0; i < 1000000; i++);
        /* QEMU debug exit: I/O port 0x501, value 0x31 → exit code (0x31*2+1)=99 */
        outb(0x501, 0x31);
        /* Fallback: triple fault to crash QEMU */
        __asm__ volatile("cli; hlt");
    }

    /* Check for CTX magic — context store write */
    if (code_len >= 7 && body_ptr[0] == 'C' && body_ptr[1] == 'T' && body_ptr[2] == 'X') {
        /* CTX packet: "CTX" + type(1) + timestamp(4) + data */
        u8 ctx_type = body_ptr[3];
        u32 ctx_ts = *(u32*)(body_ptr + 3);  /* bytes 3-6: timestamp LE ... actually: */
        /* Reparse: CTX(3) type(1) ts(4) data(N) */
        ctx_type = body_ptr[3];
        ctx_ts = body_ptr[4] | (body_ptr[5]<<8) | (body_ptr[6]<<16) | (body_ptr[7]<<24);
        int ctx_data_len = code_len - 8;
        if (ctx_data_len > 248) ctx_data_len = 248;

        int seq = ctx_store_append(ctx_type, ctx_ts, (char*)(body_ptr + 8), ctx_data_len);

        u8 resp[128]; int rl = 0;
        const char *rp = "HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nstored=";
        while (*rp) resp[rl++] = *rp++;
        char nb2[12]; int ni2 = 0;
        u32 sv = (seq >= 0) ? seq : 0;
        if (sv == 0) nb2[ni2++] = '0';
        else { while (sv > 0) { nb2[ni2++] = '0' + sv%10; sv /= 10; } }
        while (ni2 > 0) resp[rl++] = nb2[--ni2];
        rp = ",entries="; while (*rp) resp[rl++] = *rp++;
        ni2 = 0; sv = ctx_header.entry_count;
        if (sv == 0) nb2[ni2++] = '0';
        else { while (sv > 0) { nb2[ni2++] = '0' + sv%10; sv /= 10; } }
        while (ni2 > 0) resp[rl++] = nb2[--ni2];
        resp[rl++] = '\n';
        tcp_send(remote_mac, remote_ip, remote_port, TCP_ACK | TCP_PSH | TCP_FIN, resp, rl);
        return;
    }

    /* Check for GET /store — read context entries */
    {
        int is_store_get = 0;
        for (int i = 0; i < http_buf_len - 5; i++) {
            if (http_buf[i]=='/' && http_buf[i+1]=='s' && http_buf[i+2]=='t' &&
                http_buf[i+3]=='o' && http_buf[i+4]=='r' && http_buf[i+5]=='e') {
                is_store_get = 1; break;
            }
        }
        if (is_store_get && !is_post) {
            /* Return recent entries as text */
            static u8 resp[1400];
            int rl = 0;
            const char *rp = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n";
            while (*rp) resp[rl++] = *rp++;

            ctx_entry_t entries[5];
            int count = ctx_store_read_recent(entries, 5);
            for (int i = 0; i < count && rl < 1300; i++) {
                /* [seq] type=N ts=N data */
                resp[rl++] = '[';
                char nb2[12]; int ni2 = 0;
                u32 sv = entries[i].timestamp;
                if (sv == 0) nb2[ni2++] = '0';
                else { while (sv > 0) { nb2[ni2++] = '0' + sv%10; sv /= 10; } }
                while (ni2 > 0) resp[rl++] = nb2[--ni2];
                resp[rl++] = ']'; resp[rl++] = ' ';
                for (int j = 0; j < entries[i].len && j < 248 && rl < 1300; j++)
                    resp[rl++] = entries[i].data[j];
                resp[rl++] = '\n';
            }
            if (count == 0) {
                rp = "(empty)\n"; while (*rp) resp[rl++] = *rp++;
            }
            tcp_send(remote_mac, remote_ip, remote_port, TCP_ACK | TCP_PSH | TCP_FIN, resp, rl);
            return;
        }
    }

    /* Check for MON magic — monitor registration */

    if (code_len >= 17 && body_ptr[0] == MON_MAGIC_0 &&
        body_ptr[1] == MON_MAGIC_1 && body_ptr[2] == MON_MAGIC_2) {
        /* MON packet: magic(3) + interval_ms(4) + cond_op(1) + cond_val(4) +
         * hub_ip(4) + hub_port(2) + code_len(2) + code(N) = 20 + N */
        u32 interval = *(u32*)(body_ptr + 3);
        u8  cond_op  = body_ptr[7];
        u32 cond_val = *(u32*)(body_ptr + 8);
        u32 hub_ip   = *(u32*)(body_ptr + 12);
        u16 hub_port = *(u16*)(body_ptr + 16);
        u16 mon_code_len = *(u16*)(body_ptr + 18);
        u8 *mon_code = body_ptr + 20;

        /* Convert interval_ms to poll ticks (~1 tick per main loop iteration) */
        /* Rough estimate: 1 tick ≈ 1ms in QEMU polling loop */
        u32 ticks = interval > 0 ? interval : 1000;

        int slot = monitor_register(mon_code, mon_code_len, ticks,
                                    cond_op, cond_val, hub_ip, hub_port);

        u8 resp[256]; int rl = 0;
        const char *rp = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n";
        while (*rp) resp[rl++] = *rp++;
        if (slot >= 0) {
            rp = "monitor="; while (*rp) resp[rl++] = *rp++;
            resp[rl++] = '0' + slot;
            rp = ",ok\n"; while (*rp) resp[rl++] = *rp++;
        } else {
            rp = "error: no monitor slots\n"; while (*rp) resp[rl++] = *rp++;
        }
        tcp_send(remote_mac, remote_ip, remote_port, TCP_ACK | TCP_PSH | TCP_FIN, resp, rl);
        return;
    }

    /* Copy body (raw machine code) to code buffer */
    if (code_len > CODE_BUF_SIZE) code_len = CODE_BUF_SIZE;
    mem_copy(code_buf, body_ptr, code_len);

    serial_print("[POKE] Code received: ");
    serial_hex(code_len);
    serial_print(" bytes\n");

    /* Only execute if body looks like valid code (not text like 'test') */
    /* For safety: require at least a RET (0xC3) somewhere */
    int has_ret = 0;
    for (int i = 0; i < code_len; i++) {
        if (code_buf[i] == 0xC3) { has_ret = 1; break; }
    }

    /* Clear result buffer */
    mem_set(result_buf, 0, RESULT_BUF_SIZE);
    result_len = 0;
    u32 ret_eax = 0;

    /* Guard: reject if first byte is HLT (0xF4) or CLI (0xFA).
     * Full byte scan has false positives (jump offsets contain 0xFA).
     * Hub-side asm.js guard does proper instruction-level scanning. */
    if (has_ret && code_len > 0 && (code_buf[0] == 0xF4 || code_buf[0] == 0xFA)) {
        has_ret = 0;
    }

    if (has_ret) {
        serial_print("[POKE] Executing code...\n");
        u32 (*code_fn)(char *rbuf, int *rlen) = (u32 (*)(char *, int *))code_buf;
        ret_eax = code_fn(result_buf, &result_len);
        serial_print("[POKE] Execution complete, eax=");
        serial_hex(ret_eax);
        serial_print("\n");
    } else {
        serial_print("[POKE] Rejected\n");
    }

    /* Build HTTP response */
    u8 resp[512]; mem_set(resp, 0, 512);
    int rlen = 0;
    const char *rp;
    rp = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n";
    while (*rp) resp[rlen++] = *rp++;

    if (!has_ret) {
        rp = "no RET found, not executed\n";
        while (*rp) resp[rlen++] = *rp++;
    } else if (result_len > 0) {
        /* Return whatever the code wrote to result_buf */
        for (int i = 0; i < result_len && rlen < 500; i++)
            resp[rlen++] = result_buf[i];
    } else {
        /* Return EAX as decimal */
        rp = "eax=";
        while (*rp) resp[rlen++] = *rp++;
        /* decimal conversion */
        char dbuf[12];
        int di = 0;
        u32 v = ret_eax;
        if (v == 0) { dbuf[di++] = '0'; }
        else { while (v > 0) { dbuf[di++] = '0' + (v % 10); v /= 10; } }
        while (di > 0) resp[rlen++] = dbuf[--di];
        resp[rlen++] = '\n';
    }

    tcp_send(remote_mac, remote_ip, remote_port, TCP_ACK | TCP_PSH | TCP_FIN, resp, rlen);
    serial_print("[POKE] Response sent\n");
}

void handle_tcp(u8 *pkt, int len) {
    struct eth_header *eth = (struct eth_header *)pkt;
    struct ip_header *ip = (struct ip_header *)(pkt + 14);
    struct tcp_header *tcp = (struct tcp_header *)(pkt + 34);

    u16 src_port = ((tcp->src_port & 0xFF) << 8) | ((tcp->src_port >> 8) & 0xFF);
    u16 dst_port = ((tcp->dst_port & 0xFF) << 8) | ((tcp->dst_port >> 8) & 0xFF);

    serial_print("[TCP] dst="); serial_hex(dst_port);
    serial_print(" flags="); serial_hex(tcp->flags);
    serial_print("\n");

    if (dst_port != tcp_local_port) return;

    int tcp_hdr_len = (tcp->data_offset >> 4) * 4;
    int ip_total = ((ip->total_len & 0xFF) << 8) | ((ip->total_len >> 8) & 0xFF);
    int data_len = ip_total - 20 - tcp_hdr_len;
    u8 *data = pkt + 34 + tcp_hdr_len;

    u32 their_seq = __builtin_bswap32(tcp->seq);
    u32 their_ack = __builtin_bswap32(tcp->ack);

    /* Save remote info */
    mem_copy(remote_mac, eth->src, 6);
    mem_copy(remote_ip, ip->src, 4);
    remote_port = tcp->src_port; /* keep network order */

    if (tcp->flags & TCP_SYN) {
        /* Debug: show SYN received on line 3 */
        u16 *tdbg = (u16 *)(VGA_BASE + (VGA_WIDTH * 3) * 2);
        tdbg[0]=(0x0A<<8)|'S'; tdbg[1]=(0x0A<<8)|'Y'; tdbg[2]=(0x0A<<8)|'N';
        tdbg[3]=(0x0A<<8)|' ';
        tdbg[4]=(0x0A<<8)|('0'+remote_ip[0]/100);
        tdbg[5]=(0x0A<<8)|('0'+(remote_ip[0]/10)%10);
        tdbg[6]=(0x0A<<8)|('0'+remote_ip[0]%10);
        tdbg[7]=(0x0A<<8)|'.';
        tdbg[8]=(0x0A<<8)|('0'+remote_ip[3]/10);
        tdbg[9]=(0x0A<<8)|('0'+remote_ip[3]%10);

        /* SYN received → SYN+ACK */
        tcp_remote_seq = their_seq + 1;
        tcp_local_seq = 5000;
        serial_print("[TCP] SYN recv, sending SYN+ACK\n");
        tcp_send(remote_mac, remote_ip, remote_port, TCP_SYN | TCP_ACK, 0, 0);
        serial_print("[TCP] SYN+ACK sent\n");

        tdbg[11]=(0x0B<<8)|'S'; tdbg[12]=(0x0B<<8)|'A'; tdbg[13]=(0x0B<<8)|'K';

        tcp_connected = 1;
        http_buf_len = 0;
        return;
    }

    if (tcp->flags & TCP_ACK) {
        tcp_remote_seq = their_seq + data_len;

        if (data_len > 0) {
            /* Accumulate HTTP data */
            if (http_buf_len + data_len < HTTP_BUF_SIZE) {
                mem_copy(http_buf + http_buf_len, data, data_len);
                http_buf_len += data_len;
            }

            /* ACK the data */
            tcp_send(remote_mac, remote_ip, remote_port, TCP_ACK, 0, 0);

            /* Check if HTTP request is complete */
            int header_end = -1;
            for (int i = 0; i < http_buf_len - 3; i++) {
                if (http_buf[i] == '\r' && http_buf[i+1] == '\n' &&
                    http_buf[i+2] == '\r' && http_buf[i+3] == '\n') {
                    header_end = i + 4;
                    break;
                }
            }
            if (header_end > 0) {
                /* Parse Content-Length */
                int content_length = 0;
                for (int i = 0; i < header_end - 16; i++) {
                    /* Match "ontent-Length: " (skip first char for case insensitivity) */
                    if (http_buf[i+1]=='o' && http_buf[i+2]=='n' && http_buf[i+3]=='t' &&
                        http_buf[i+4]=='e' && http_buf[i+5]=='n' && http_buf[i+6]=='t' &&
                        http_buf[i+7]=='-' &&
                        (http_buf[i+8]=='L' || http_buf[i+8]=='l') &&
                        http_buf[i+14]==':') {
                        /* Skip ": " */
                        int j = i + 15;
                        while (j < header_end && http_buf[j] == ' ') j++;
                        while (j < header_end && http_buf[j] >= '0' && http_buf[j] <= '9') {
                            content_length = content_length * 10 + (http_buf[j] - '0');
                            j++;
                        }
                        break;
                    }
                }
                serial_print("[TCP] content_length=");
                serial_hex(content_length);
                serial_print(" buf_len=");
                serial_hex(http_buf_len);
                serial_print("\n");

                int expected_total = header_end + content_length;
                serial_print("[TCP] expected=");
                serial_hex(expected_total);
                serial_print(" have=");
                serial_hex(http_buf_len);
                serial_print("\n");

                if (content_length == 0 || http_buf_len >= expected_total) {
                    handle_http();
                    http_buf_len = 0;
                } else {
                    /* Wait for more data — but set a max wait */
                    static int wait_count = 0;
                    wait_count++;
                    if (wait_count > 100) {
                        /* Timeout — process what we have */
                        serial_print("[TCP] wait timeout, processing partial\n");
                        handle_http();
                        http_buf_len = 0;
                        wait_count = 0;
                    }
                }
            }
        }
    }

    if (tcp->flags & TCP_FIN) {
        tcp_remote_seq = their_seq + 1;
        tcp_send(remote_mac, remote_ip, remote_port, TCP_ACK | TCP_FIN, 0, 0);
        tcp_connected = 0;
    }
}

/* ── Packet Handler ── */
void handle_packet(u8 *pkt, int len) {
    struct eth_header *eth = (struct eth_header *)pkt;

    if (eth->type == ETH_ARP) {
        handle_arp(pkt, len);
    } else if (eth->type == ETH_IP) {
        struct ip_header *ip = (struct ip_header *)(pkt + 14);
        if (ip->protocol == 6) { /* TCP */
            handle_tcp(pkt, len);
        }
    }
}

/* ── Shell ── */
#define CMD_BUF_SIZE 256
static char cmd_buf[CMD_BUF_SIZE];
static int cmd_len = 0;

void shell_prompt(void) {
    vga_set_color(0x0F, 0x00);
    vga_print("poke> ");
}

void shell_exec(void) {
    cmd_buf[cmd_len] = 0;

    if (str_eq(cmd_buf, "help")) {
        vga_set_color(0x0B, 0x00);
        vga_print("\ncommands: help, clear, info, net, ip");
    } else if (str_eq(cmd_buf, "clear")) {
        vga_clear();
    } else if (str_eq(cmd_buf, "info")) {
        vga_set_color(0x0E, 0x00);
        vga_print("\nPOKE OS v0.2 — inject & run. IA-32. HTTP on port 80.");
    } else if (str_eq(cmd_buf, "net")) {
        vga_set_color(0x0E, 0x00);
        vga_print("\nMAC: ");
        for (int i = 0; i < 6; i++) {
            vga_print_hex(my_mac[i]);
            if (i < 5) vga_putchar(':');
        }
        vga_print("\ne1000 base: ");
        vga_print_hex(e1000_base);
    } else if (str_eq(cmd_buf, "ip")) {
        vga_set_color(0x0E, 0x00);
        vga_print("\nIP: ");
        for (int i = 0; i < 4; i++) {
            vga_print_dec(my_ip[i]);
            if (i < 3) vga_putchar('.');
        }
        vga_print(":80");
    } else if (cmd_len > 0) {
        vga_set_color(0x0C, 0x00);
        vga_print("\nunknown: ");
        vga_print(cmd_buf);
    }

    cmd_len = 0;
    vga_print("\n");
    shell_prompt();
}

/* ── Kernel Main ── */
void kernel_main(void) {
    serial_init();
    serial_print("[POKE] kernel_main start\n");
    kb_buf_init();

    /* Force IP — static initializer may not work */
    my_ip[0] = 10; my_ip[1] = 0; my_ip[2] = 2; my_ip[3] = 15;

    vga_clear();

    vga_set_color(0x0A, 0x00);
    vga_print("  POKE OS v0.2\n");
    vga_set_color(0x07, 0x00);
    vga_print("  inject & run\n\n");

    /* Init network */
    serial_print("[POKE] PCI scan...\n");
    vga_print("  [NIC] Scanning PCI... ");
    if (e1000_find()) {
        nic_is_ne2k = 0;
        vga_set_color(0x0A, 0x00);
        vga_print("e1000 found at ");
        vga_print_hex(e1000_base);
        vga_print("\n");

        serial_print("[POKE] e1000 found at ");
        serial_hex(e1000_base);
        serial_print("\n");

        vga_set_color(0x07, 0x00);
        vga_print("  [NIC] Initializing... ");
        serial_print("[POKE] e1000_init start\n");
        e1000_init();
    } else if (ne2k_find()) {
        nic_is_ne2k = 1;
        vga_set_color(0x0A, 0x00);
        vga_print("NE2000 found at I/O ");
        vga_print_hex(ne2k_iobase);
        vga_print("\n");

        serial_print("[POKE] NE2000 found at ");
        serial_hex(ne2k_iobase);
        serial_print("\n");

        vga_set_color(0x07, 0x00);
        vga_print("  [NIC] Initializing NE2000... ");
        ne2k_init();
        serial_print("[POKE] e1000_init done\n");
        vga_set_color(0x0A, 0x00);
        vga_print("OK\n");

        vga_set_color(0x07, 0x00);
        vga_print("  [NET] IP: 10.0.2.15:80\n");
        vga_print("  [NET] Listening for HTTP POST /poke\n");
    } else {
        vga_set_color(0x0C, 0x00);
        vga_print("not found!\n");
    }

    /* Send gratuitous ARP to announce ourselves */
    {
        u8 ann[60];
        mem_set(ann, 0, 60);

        /* Ethernet: broadcast */
        mem_set(ann, 0xFF, 6);         /* dst = broadcast */
        ann[6]=my_mac[0]; ann[7]=my_mac[1]; ann[8]=my_mac[2];
        ann[9]=my_mac[3]; ann[10]=my_mac[4]; ann[11]=my_mac[5];
        ann[12]=0x08; ann[13]=0x06;    /* type = ARP */

        /* ARP request: who has gateway? tell me */
        ann[14]=0x00; ann[15]=0x01;    /* hw = ethernet */
        ann[16]=0x08; ann[17]=0x00;    /* proto = IPv4 */
        ann[18]=6; ann[19]=4;
        ann[20]=0x00; ann[21]=0x01;    /* opcode = REQUEST */

        /* sender = us */
        ann[22]=my_mac[0]; ann[23]=my_mac[1]; ann[24]=my_mac[2];
        ann[25]=my_mac[3]; ann[26]=my_mac[4]; ann[27]=my_mac[5];
        ann[28]=10; ann[29]=0; ann[30]=2; ann[31]=15;  /* 10.0.2.15 */

        /* target = gateway */
        mem_set(ann+32, 0, 6);         /* target MAC = 00:00:00:00:00:00 */
        ann[38]=10; ann[39]=0; ann[40]=2; ann[41]=2;   /* 10.0.2.2 */

        serial_print("[POKE] sending ARP announce\n");
        nic_send(ann, 60);
        serial_print("[POKE] ARP sent\n");
        vga_print("  [NET] ARP announce sent\n");
    }

    /* Init context store (disk) — disabled until ATA driver stabilized */
    /* ctx_store_init(); */
    virt_regs_init();
    monitor_init();
    vga_print("  [MON] Monitor system ready\n");
    vga_print("  [SIM] Virtual sensors active\n");

    vga_print("\n");
    shell_prompt();

    /* Main loop: shell + network + monitors */
    u8 recv_buf[2048];
    static int pkt_count = 0;
    while (1) {
        /* Poll keyboard */
        char c = kb_read();
        if (c) {
            if (c == '\r') {
                shell_exec();
            } else if (c == '\b') {
                if (cmd_len > 0) {
                    cmd_len--;
                    vga_putchar('\b');
                }
            } else if (cmd_len < CMD_BUF_SIZE - 1) {
                cmd_buf[cmd_len++] = c;
                vga_putchar(c);
            }
        }

        /* Poll network */
        int len = nic_recv(recv_buf);
        if (len > 0) {
            pkt_count++;
            if (pkt_count <= 5) { serial_print("[LOOP] pkt "); serial_hex(pkt_count); serial_print("\n"); }
            /* Debug: show packet indicator at top-right */
            u16 *dbg = (u16 *)(VGA_BASE + (VGA_WIDTH - 10) * 2);
            dbg[0] = (0x0E << 8) | 'P';
            dbg[1] = (0x0E << 8) | 'K';
            dbg[2] = (0x0E << 8) | 'T';
            dbg[3] = (0x0E << 8) | ':';
            dbg[4] = (0x0E << 8) | ('0' + (pkt_count / 100) % 10);
            dbg[5] = (0x0E << 8) | ('0' + (pkt_count / 10) % 10);
            dbg[6] = (0x0E << 8) | ('0' + pkt_count % 10);

            /* Debug: show packet type */
            struct eth_header *de = (struct eth_header *)recv_buf;
            u16 *line = (u16 *)(VGA_BASE + (VGA_WIDTH * 1 + 60) * 2);
            if (de->type == ETH_ARP) {
                line[0] = (0x0E<<8)|'A'; line[1] = (0x0E<<8)|'R'; line[2] = (0x0E<<8)|'P';
            } else if (de->type == ETH_IP) {
                struct ip_header *di = (struct ip_header *)(recv_buf + 14);
                line[0] = (0x0E<<8)|'I'; line[1] = (0x0E<<8)|'P';
                line[2] = (0x0E<<8)|('0' + di->protocol / 10);
                line[3] = (0x0E<<8)|('0' + di->protocol % 10);
            } else {
                line[0] = (0x0E<<8)|'?'; line[1] = (0x0E<<8)|'?';
            }

            handle_packet(recv_buf, len);
        }

        /* Simulate sensors + monitor tick */
        virt_regs_tick();
        monitor_tick();

        /* Fire pending events to hub */
        if (pending_event >= 0) {
            fire_event_to_hub(pending_event, pending_event_value);
            pending_event = -1;
        }
    }
}
