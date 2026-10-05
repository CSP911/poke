/* SD card over SDHCI (BCM2711 EMMC2) — shared by the incubation probe and
 * the resident driver. PIO only, default speed (25 MHz), 4-bit bus, no 1.8 V
 * switching, one 512-byte block per command. Enough for a skill library.
 *
 * The includer provides:   static void sd_log(const char *s);
 *                          static long sd_mbox(void *buf, long len);  (0 = ok)
 * and may set EMMC_BASE (Pi 4 hardware: 0xFE340000; QEMU's raspi4b puts the
 * card on the older controller at 0xFE300000 — both are SDHCI). */
#ifndef POKE_SDHCI_H
#define POKE_SDHCI_H
typedef unsigned char sd_u8; typedef unsigned int sd_u32;

#ifndef EMMC_BASE
#define EMMC_BASE 0xFE340000UL
#endif
#define SDR(off) (*(volatile sd_u32 *)(EMMC_BASE + (off)))
#define SD_BLKSIZECNT 0x04
#define SD_ARG1 0x08
#define SD_CMDTM 0x0C
#define SD_RESP0 0x10
#define SD_DATA 0x20
#define SD_STATUS 0x24
#define SD_CONTROL0 0x28
#define SD_CONTROL1 0x2C
#define SD_INTERRUPT 0x30
#define SD_IRPT_MASK 0x34
#define SD_IRPT_EN 0x38
#define SD_CONTROL2 0x3C
#define SD_SLOTISR_VER 0xFC

typedef struct {
    int sdhc;                 /* 1: block addressing (SDHC/SDXC), 0: byte addressing */
    sd_u32 rca;
    sd_u32 capacity_mb;
    sd_u32 cid[4];
    sd_u32 resp[4];
    sd_u32 base_hz;
    int bits;                 /* bus width */
} sd_t;

/* tiny log line builder */
static char sdl_buf[100] = "x"; static int sdl_n;
static void sdl_c(char c) { if (sdl_n < 99) sdl_buf[sdl_n++] = c; }
static void sdl_s(const char *s) { while (*s) sdl_c(*s++); }
static void sdl_h(sd_u32 v, int d) { for (int i = d - 1; i >= 0; i--) sdl_c("0123456789abcdef"[(v >> (i * 4)) & 0xf]); }
static void sdl_d(sd_u32 v) { char t[12]; int n = 0; if (!v) t[n++] = '0'; while (v) { t[n++] = '0' + v % 10; v /= 10; } while (n) sdl_c(t[--n]); }
static void sdl_end(void) { sdl_buf[sdl_n] = 0; sd_log(sdl_buf); sdl_n = 0; }

static int sd_wait(sd_u32 off, sd_u32 mask, sd_u32 want, int ms) {
    for (int i = 0; i < ms * 10; i++) { if ((SDR(off) & mask) == want) return 0; for (volatile int k = 0; k < 400; k++) {} }
    return -1;
}
static void sd_spin_ms(int ms) { for (int i = 0; i < ms; i++) for (volatile int k = 0; k < 4000; k++) {} }

static sd_u32 __attribute__((aligned(16))) sd_mb[9] = { 1 };
static sd_u32 sd_base_clock(void) {                        /* mailbox: clock id 12 = EMMC2 */
    sd_mb[0] = 9 * 4; sd_mb[1] = 0; sd_mb[2] = 0x00030002; sd_mb[3] = 8; sd_mb[4] = 0; sd_mb[5] = 12; sd_mb[6] = 0; sd_mb[7] = 0; sd_mb[8] = 0;
    if (sd_mbox(sd_mb, 9 * 4) != 0 || sd_mb[6] == 0) return 100000000;   /* Pi 4 EMMC2 default */
    return sd_mb[6];
}

static int sd_set_clock(sd_t *sd, sd_u32 hz) {
    SDR(SD_CONTROL1) &= ~(1u << 2);
    sd_u32 base = sd->base_hz, div = 0;
    if (base > hz) { div = base / (2 * hz); if (div * 2 * hz < base) div++; }
    if (div > 1023) div = 1023;
    sd_u32 c1 = SDR(SD_CONTROL1) & ~(0xFFu << 8) & ~(3u << 6) & ~(0xFu << 16);
    c1 |= ((div & 0xFF) << 8) | (((div >> 8) & 3) << 6) | (0xEu << 16) | 1;
    SDR(SD_CONTROL1) = c1;
    if (sd_wait(SD_CONTROL1, 1u << 1, 1u << 1, 100)) { sd_log("sd: clock not stable"); return -1; }
    SDR(SD_CONTROL1) |= 1u << 2;
    sdl_s("sd: clock "); sdl_d(div ? base / (2 * div) / 1000 : base / 1000); sdl_s(" kHz (base "); sdl_d(base / 1000000); sdl_s(" MHz, div "); sdl_d(div); sdl_s(")"); sdl_end();
    return 0;
}

#define SD_RT_NONE 0
#define SD_RT_136 1
#define SD_RT_48 2
#define SD_RT_48B 3
#define SD_DATA_PRESENT (1u << 21)
#define SD_DIR_READ (1u << 4)
static int sd_cmd(sd_t *sd, sd_u32 idx, sd_u32 arg, sd_u32 rtype, sd_u32 flags) {
    if (sd_wait(SD_STATUS, 1, 0, 500)) { sdl_s("sd: cmd"); sdl_d(idx); sdl_s(" CMD_INHIBIT stuck"); sdl_end(); return -1; }
    if ((rtype == SD_RT_48B || (flags & SD_DATA_PRESENT)) && sd_wait(SD_STATUS, 2, 0, 500)) { sdl_s("sd: cmd"); sdl_d(idx); sdl_s(" DAT_INHIBIT stuck"); sdl_end(); return -1; }
    SDR(SD_INTERRUPT) = 0xFFFFFFFF;
    SDR(SD_ARG1) = arg;
    sd_u32 tm = (idx << 24) | (rtype << 16) | flags;
    if (rtype != SD_RT_NONE) tm |= (1u << 19);
    if (rtype == SD_RT_48 || rtype == SD_RT_48B) tm |= (1u << 20);
    if (idx == 41 || idx == 2 || idx == 9) tm &= ~((1u << 19) | (1u << 20));
    SDR(SD_CMDTM) = tm;
    if (sd_wait(SD_INTERRUPT, 0x8001, 1, 1000) && !(SDR(SD_INTERRUPT) & 0x8000)) {
        sdl_s("sd: cmd"); sdl_d(idx); sdl_s(" timeout, INTERRUPT="); sdl_h(SDR(SD_INTERRUPT), 8); sdl_s(" STATUS="); sdl_h(SDR(SD_STATUS), 8); sdl_end(); return -1;
    }
    sd_u32 irq = SDR(SD_INTERRUPT);
    if (irq & 0x8000) { sdl_s("sd: cmd"); sdl_d(idx); sdl_s(" error, INTERRUPT="); sdl_h(irq, 8); sdl_end(); SDR(SD_INTERRUPT) = irq; return -1; }
    SDR(SD_INTERRUPT) = 1;
    for (int i = 0; i < 4; i++) sd->resp[i] = SDR(SD_RESP0 + i * 4);
    return 0;
}
static int sd_acmd(sd_t *sd, sd_u32 idx, sd_u32 arg, sd_u32 rtype) { if (sd_cmd(sd, 55, sd->rca << 16, SD_RT_48, 0)) return -1; return sd_cmd(sd, idx, arg, rtype, 0); }

static int sd_init(sd_t *sd) {
    sd->sdhc = 0; sd->rca = 0; sd->capacity_mb = 0; sd->bits = 1;
    sd_u32 ver = SDR(SD_SLOTISR_VER), st = SDR(SD_STATUS);
    sdl_s("sd: sdhci v"); sdl_d(((ver >> 16) & 0xff) + 1); sdl_s(" @"); sdl_h((sd_u32)(EMMC_BASE >> 4), 7); sdl_s("0, STATUS="); sdl_h(st, 8);
    sdl_s((st & (1u << 16)) ? ", card inserted" : ", NO CARD"); sdl_end();
    if (!(st & (1u << 16))) return -1;
    SDR(SD_CONTROL1) |= (1u << 24);
    if (sd_wait(SD_CONTROL1, 1u << 24, 0, 100)) { sd_log("sd: reset never cleared"); return -2; }
    SDR(SD_CONTROL0) = (0x7u << 9) | (1u << 8);
    SDR(SD_CONTROL2) = 0;
    sd->base_hz = sd_base_clock();
    if (sd_set_clock(sd, 400000)) return -3;
    SDR(SD_IRPT_EN) = 0xFFFFFFFF; SDR(SD_IRPT_MASK) = 0xFFFFFFFF; SDR(SD_INTERRUPT) = 0xFFFFFFFF;
    sd_spin_ms(10);
    if (sd_cmd(sd, 0, 0, SD_RT_NONE, 0)) return -4;
    int v2 = (sd_cmd(sd, 8, 0x1AA, SD_RT_48, 0) == 0) && (sd->resp[0] & 0xFFF) == 0x1AA;
    int tries = 0;
    for (;;) {
        if (sd_acmd(sd, 41, 0x50FF8000 | (v2 ? (1u << 30) : 0), SD_RT_48)) return -5;
        if (sd->resp[0] & (1u << 31)) { sd->sdhc = (sd->resp[0] >> 30) & 1; break; }
        if (++tries > 200) { sd_log("sd: card never ready"); return -6; }
        sd_spin_ms(10);
    }
    if (sd_cmd(sd, 2, 0, SD_RT_136, 0)) return -7;
    for (int i = 0; i < 4; i++) sd->cid[i] = sd->resp[i];
    if (sd_cmd(sd, 3, 0, SD_RT_48, 0)) return -8;
    sd->rca = sd->resp[0] >> 16;
    if (sd_cmd(sd, 9, sd->rca << 16, SD_RT_136, 0) == 0) {
        if (((sd->resp[3] >> 22) & 3) == 1) { sd_u32 c = (sd->resp[1] >> 8) & 0x3FFFFF; sd->capacity_mb = (c + 1) / 2; }   /* CSD v2: C_SIZE[21:0] = CSD[69:48] → resp[1][29:8] */
        else {   /* CSD v1: capacity = (C_SIZE+1) * 2^(C_SIZE_MULT+2) * 2^READ_BL_LEN */
            sd_u32 bl = (sd->resp[2] >> 8) & 0xF, cs = ((sd->resp[2] & 0x3) << 10) | (sd->resp[1] >> 22), mult = (sd->resp[1] >> 7) & 7;
            sd->capacity_mb = (sd_u32)(((unsigned long)(cs + 1) << (mult + 2 + bl)) >> 20);
        }
    }
    if (sd_cmd(sd, 7, sd->rca << 16, SD_RT_48B, 0)) return -9;
    if (sd_set_clock(sd, 25000000)) return -10;
    if (sd_acmd(sd, 6, 2, SD_RT_48) == 0) { SDR(SD_CONTROL0) |= (1u << 1); sd->bits = 4; }
    if (!sd->sdhc) sd_cmd(sd, 16, 512, SD_RT_48, 0);
    { sd_u8 *c = (sd_u8 *)sd->cid; char nm[6] = { (char)c[12], (char)c[11], (char)c[10], (char)c[9], (char)c[8], 0 };
      for (int i = 0; i < 5; i++) if (nm[i] < 0x20 || nm[i] > 0x7e) nm[i] = '?';     /* keep logs printable */
      sdl_s("sd: "); sdl_s(sd->sdhc ? "SDHC" : "SDSC"); sdl_s(" '"); sdl_s(nm); sdl_s("' "); sdl_d(sd->capacity_mb); sdl_s(" MB, rca 0x"); sdl_h(sd->rca, 4); sdl_s(", "); sdl_d(sd->bits); sdl_s("-bit, "); sdl_d(tries); sdl_s(" polls"); sdl_end(); }
    return 0;
}

static int sd_read(sd_t *sd, sd_u32 lba, sd_u32 *buf) {
    SDR(SD_BLKSIZECNT) = (1u << 16) | 512;
    if (sd_cmd(sd, 17, sd->sdhc ? lba : lba * 512, SD_RT_48, SD_DATA_PRESENT | SD_DIR_READ)) return -1;
    if (sd_wait(SD_INTERRUPT, 0x8020, 0x20, 1000)) { sdl_s("sd: read "); sdl_d(lba); sdl_s(" no READ_RDY, INTERRUPT="); sdl_h(SDR(SD_INTERRUPT), 8); sdl_end(); return -2; }
    SDR(SD_INTERRUPT) = 0x20;
    for (int i = 0; i < 128; i++) buf[i] = SDR(SD_DATA);
    if (sd_wait(SD_INTERRUPT, 0x8002, 0x2, 1000)) { sdl_s("sd: read "); sdl_d(lba); sdl_s(" no DATA_DONE"); sdl_end(); return -3; }
    SDR(SD_INTERRUPT) = 2;
    return 0;
}
static int sd_write(sd_t *sd, sd_u32 lba, const sd_u32 *buf) {
    SDR(SD_BLKSIZECNT) = (1u << 16) | 512;
    if (sd_cmd(sd, 24, sd->sdhc ? lba : lba * 512, SD_RT_48, SD_DATA_PRESENT)) return -1;
    if (sd_wait(SD_INTERRUPT, 0x8010, 0x10, 1000)) { sdl_s("sd: write "); sdl_d(lba); sdl_s(" no WRITE_RDY, INTERRUPT="); sdl_h(SDR(SD_INTERRUPT), 8); sdl_end(); return -2; }
    SDR(SD_INTERRUPT) = 0x10;
    for (int i = 0; i < 128; i++) SDR(SD_DATA) = buf[i];
    if (sd_wait(SD_INTERRUPT, 0x8002, 0x2, 3000)) { sdl_s("sd: write "); sdl_d(lba); sdl_s(" no DATA_DONE"); sdl_end(); return -3; }
    SDR(SD_INTERRUPT) = 2;
    return 0;
}
#endif
