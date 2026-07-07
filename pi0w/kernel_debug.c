/* Debug: LED blink + UART init step by step */

typedef unsigned int u32;

#define PERI_BASE   0x20000000
#define GPIO_BASE   (PERI_BASE + 0x200000)
#define GPFSEL1     (*(volatile u32 *)(GPIO_BASE + 0x04))
#define GPFSEL4     (*(volatile u32 *)(GPIO_BASE + 0x10))
#define GPSET0      (*(volatile u32 *)(GPIO_BASE + 0x1C))
#define GPSET1      (*(volatile u32 *)(GPIO_BASE + 0x20))
#define GPCLR0      (*(volatile u32 *)(GPIO_BASE + 0x28))
#define GPCLR1      (*(volatile u32 *)(GPIO_BASE + 0x2C))

#define AUX_BASE    (PERI_BASE + 0x215000)
#define AUX_ENABLES (*(volatile u32 *)(AUX_BASE + 0x04))
#define AUX_MU_IO   (*(volatile u32 *)(AUX_BASE + 0x40))
#define AUX_MU_IER  (*(volatile u32 *)(AUX_BASE + 0x44))
#define AUX_MU_IIR  (*(volatile u32 *)(AUX_BASE + 0x48))
#define AUX_MU_LCR  (*(volatile u32 *)(AUX_BASE + 0x4C))
#define AUX_MU_MCR  (*(volatile u32 *)(AUX_BASE + 0x50))
#define AUX_MU_LSR  (*(volatile u32 *)(AUX_BASE + 0x54))
#define AUX_MU_CNTL (*(volatile u32 *)(AUX_BASE + 0x60))
#define AUX_MU_BAUD (*(volatile u32 *)(AUX_BASE + 0x68))

static void delay(int n) {
    for (volatile int i = 0; i < n; i++) {}
}

static void led_setup(void) {
    GPFSEL4 &= ~(7 << 21);
    GPFSEL4 |= (1 << 21);
}

static void led_on(void) { GPSET1 = (1 << (47-32)); }
static void led_off(void) { GPCLR1 = (1 << (47-32)); }

static void blink(int times) {
    for (int i = 0; i < times; i++) {
        led_on(); delay(1000000);
        led_off(); delay(1000000);
    }
    delay(3000000); /* pause */
}

void kernel_main(void) {
    led_setup();

    blink(1); /* 1 = alive */

    /* UART Step 1: enable */
    AUX_ENABLES = 1;
    blink(2); /* 2 = AUX enabled */

    /* UART Step 2: configure */
    AUX_MU_IER = 0;
    AUX_MU_CNTL = 0;
    AUX_MU_LCR = 3;
    AUX_MU_MCR = 0;
    AUX_MU_IIR = 0xC6;
    AUX_MU_BAUD = 270;
    blink(3); /* 3 = UART configured */

    /* UART Step 3: GPIO alt function */
    u32 sel = GPFSEL1;
    sel &= ~(7 << 12);
    sel |= (2 << 12);
    sel &= ~(7 << 15);
    sel |= (2 << 15);
    GPFSEL1 = sel;
    blink(4); /* 4 = GPIO set */

    /* UART Step 4: enable TX/RX */
    AUX_MU_CNTL = 3;
    blink(5); /* 5 = UART ready */

    /* UART Step 5: send character */
    while (!(AUX_MU_LSR & 0x20)) {}
    AUX_MU_IO = 'P';
    blink(6); /* 6 = sent char */

    /* Step 7: print string */
    {
        const char *msg = "POKE\r\n";
        while (*msg) {
            while (!(AUX_MU_LSR & 0x20)) {}
            AUX_MU_IO = *msg++;
        }
    }
    blink(7); /* 7 = string printed */

    /* Step 8: large code_buf (4KB on stack? no, static) */
    static unsigned char test_buf[4096];
    test_buf[0] = 0x42;
    test_buf[4095] = 0x42;
    blink(8); /* 8 = large buffer OK */

    /* Success — blink forever */
    while (1) {
        blink(10);
    }
}
