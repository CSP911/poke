/* Minimal Pi Zero W test — just turn on ACT LED */

typedef unsigned int u32;

#define PERI_BASE   0x20000000
#define GPIO_BASE   (PERI_BASE + 0x200000)

/* Pi Zero W ACT LED = GPIO47 */
#define GPFSEL4     (*(volatile u32 *)(GPIO_BASE + 0x10))
#define GPSET1      (*(volatile u32 *)(GPIO_BASE + 0x20))
#define GPCLR1      (*(volatile u32 *)(GPIO_BASE + 0x2C))

/* Or maybe GPIO16 on some boards */
#define GPFSEL1     (*(volatile u32 *)(GPIO_BASE + 0x04))
#define GPSET0      (*(volatile u32 *)(GPIO_BASE + 0x1C))
#define GPCLR0      (*(volatile u32 *)(GPIO_BASE + 0x28))

void kernel_main(void) {
    /* Try GPIO47 (ACT LED on Pi Zero W rev 1.3+) */
    GPFSEL4 &= ~(7 << 21);
    GPFSEL4 |= (1 << 21);   /* GPIO47 = output */
    GPSET1 = (1 << (47-32)); /* GPIO47 ON */

    /* Also try GPIO16 (ACT LED on older Pi Zero) */
    GPFSEL1 &= ~(7 << 18);
    GPFSEL1 |= (1 << 18);   /* GPIO16 = output */
    GPCLR0 = (1 << 16);     /* GPIO16 ON (active low on some boards) */

    /* Blink both forever */
    while (1) {
        GPSET1 = (1 << (47-32));
        GPSET0 = (1 << 16);
        for (volatile int i = 0; i < 2000000; i++) {}

        GPCLR1 = (1 << (47-32));
        GPCLR0 = (1 << 16);
        for (volatile int i = 0; i < 2000000; i++) {}
    }
}
