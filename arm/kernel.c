/* ============================================
 * POKE OS ARM64 — Kernel
 * UART (console + network) + Audio + Code Execution
 * + GPIO + Temp + Event Monitor
 * Target: QEMU virt machine (aarch64)
 * ============================================ */

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long u64;

/* ── PL011 UART0 (console: 0x09000000) ── */
#define UART0_BASE 0x09000000
#define UART0_DR   (*(volatile u32 *)(UART0_BASE + 0x00))
#define UART0_FR   (*(volatile u32 *)(UART0_BASE + 0x18))

/* ── PL011 UART1 (network: 0x09040000) ── */
#define UART1_BASE 0x09040000
#define UART1_DR   (*(volatile u32 *)(UART1_BASE + 0x00))
#define UART1_FR   (*(volatile u32 *)(UART1_BASE + 0x18))
#define UART1_IBRD (*(volatile u32 *)(UART1_BASE + 0x24))
#define UART1_FBRD (*(volatile u32 *)(UART1_BASE + 0x28))
#define UART1_LCR  (*(volatile u32 *)(UART1_BASE + 0x2C))
#define UART1_CR   (*(volatile u32 *)(UART1_BASE + 0x30))

/* ── Console UART ── */
void uart_putc(char c) {
    while (UART0_FR & (1 << 5));
    UART0_DR = c;
}

void uart_print(const char *s) {
    while (*s) {
        if (*s == '\n') uart_putc('\r');
        uart_putc(*s++);
    }
}

void uart_hex(u64 val) {
    const char hex[] = "0123456789ABCDEF";
    uart_print("0x");
    for (int i = 60; i >= 0; i -= 4)
        uart_putc(hex[(val >> i) & 0xF]);
}

void uart_hex32(u32 val) {
    const char hex[] = "0123456789ABCDEF";
    for (int i = 28; i >= 0; i -= 4)
        uart_putc(hex[(val >> i) & 0xF]);
}

void uart_dec(u64 val) {
    char buf[20];
    int i = 0;
    if (val == 0) { uart_putc('0'); return; }
    while (val > 0) { buf[i++] = '0' + (val % 10); val /= 10; }
    while (i > 0) uart_putc(buf[--i]);
}

char uart_getc(void) {
    if (UART0_FR & (1 << 4)) return 0;
    return UART0_DR & 0xFF;
}

/* ── Network UART (serial ↔ TCP bridge) ── */
void net_init(void) {
    /* UART1 is configured by QEMU, just enable */
    UART1_CR = 0;           /* disable */
    UART1_LCR = (3 << 5);  /* 8N1 */
    UART1_CR = (1 << 0) | (1 << 8) | (1 << 9); /* enable + TX + RX */
}

void net_putc(char c) {
    while (UART1_FR & (1 << 5));
    UART1_DR = c;
}

void net_write(const u8 *data, int len) {
    for (int i = 0; i < len; i++) net_putc(data[i]);
}

void net_print(const char *s) {
    while (*s) net_putc(*s++);
}

int net_available(void) {
    return !(UART1_FR & (1 << 4));
}

u8 net_getc(void) {
    return UART1_DR & 0xFF;
}

int net_read(u8 *buf, int maxlen) {
    int n = 0;
    while (n < maxlen && net_available()) {
        buf[n++] = net_getc();
    }
    return n;
}

/* ── Memory utils ── */
void mem_copy(void *dst, const void *src, int n) {
    u8 *d = dst; const u8 *s = src;
    while (n--) *d++ = *s++;
}

void mem_set(void *dst, u8 val, int n) {
    u8 *d = dst;
    while (n--) *d++ = val;
}

int str_eq(const char *a, const char *b) {
    while (*a && *b) { if (*a++ != *b++) return 0; }
    return *a == *b;
}

int str_len(const char *s) {
    int n = 0; while (*s++) n++; return n;
}

/* Append decimal number to buffer, return new length */
static int buf_dec(char *buf, int pos, u32 val) {
    char tmp[12];
    int ti = 0;
    if (val == 0) { buf[pos++] = '0'; return pos; }
    while (val > 0) { tmp[ti++] = '0' + (val % 10); val /= 10; }
    while (ti > 0) buf[pos++] = tmp[--ti];
    return pos;
}

/* Append string to buffer, return new length */
static int buf_str(char *buf, int pos, const char *s) {
    while (*s) buf[pos++] = *s++;
    return pos;
}

/* ── GPIO Virtual Registers ── */
/*
 * Virtual GPIO at 0x50000000 (in RAM, no real hardware).
 * 11 pins, each stored as u32.
 * Used for testing edge logic without physical GPIO.
 */
#define GPIO_BASE  0x50000000
#define GPIO_PINS  11
static u32 gpio_pins[GPIO_PINS];

void gpio_init(void) {
    for (int i = 0; i < GPIO_PINS; i++)
        gpio_pins[i] = 0;
}

void gpio_set(u32 pin, u32 value) {
    if (pin < GPIO_PINS)
        gpio_pins[pin] = value;
}

u32 gpio_get(u32 pin) {
    if (pin < GPIO_PINS)
        return gpio_pins[pin];
    return 0;
}

/* ── Virtual Temperature Sensor ── */
/*
 * Simulated temperature based on a tick counter.
 * Oscillates around 22C with small drift.
 */
static u32 virt_temp = 22;   /* degrees C */
static u32 temp_tick = 0;

void temp_tick_update(void) {
    temp_tick++;
    /* Update temperature every ~500000 poll cycles */
    if (temp_tick % 500000 == 0) {
        /* Simple simulation: oscillate 18-30C based on gpio_pins[0] as "heater" */
        u32 heater = gpio_pins[0];  /* pin 0 = heater power level */
        if (heater > 0 && virt_temp < 45)
            virt_temp += 1;
        else if (heater == 0 && virt_temp > 18)
            virt_temp -= 1;
        /* Clamp */
        if (virt_temp > 80) virt_temp = 80;
        if (virt_temp < 10) virt_temp = 10;
    }
}

/* ── Event Monitor System ── */
/*
 * Monitors watch conditions and emit EVNT frames when triggered.
 *   type=0x01: GPIO monitor — watch a pin for a threshold
 *   type=0x02: Temp monitor — watch temperature for a threshold
 *
 * Monitor packet (EMON command):
 *   "EMON" + type(1) + pin_or_0(1) + op(1) + threshold(4) + interval(4)
 *   Total: 4 + 1 + 1 + 1 + 4 + 4 = 15 bytes
 *
 * Condition ops: 0=GT, 1=LT, 2=EQ, 3=NE
 */

#define MAX_MONITORS 4

#define MON_TYPE_GPIO 0x01
#define MON_TYPE_TEMP 0x02

#define MON_OP_GT 0
#define MON_OP_LT 1
#define MON_OP_EQ 2
#define MON_OP_NE 3

typedef struct {
    u8  active;
    u8  type;             /* MON_TYPE_GPIO or MON_TYPE_TEMP */
    u8  pin;              /* GPIO pin index (for type=GPIO) */
    u8  condition_op;     /* GT, LT, EQ, NE */
    u32 condition_val;    /* threshold value */
    u32 interval_ticks;   /* check interval in poll cycles */
    u32 ticks_remaining;  /* countdown */
    u32 trigger_count;    /* total triggers */
    u32 last_value;       /* last sampled value */
    u8  fired;            /* set when triggered, cleared when condition clears */
} monitor_t;

static monitor_t monitors[MAX_MONITORS];
static u32 monitor_poll_counter = 0;

void monitor_init(void) {
    for (int i = 0; i < MAX_MONITORS; i++) {
        monitors[i].active = 0;
        monitors[i].trigger_count = 0;
        monitors[i].fired = 0;
    }
}

int monitor_register(u8 type, u8 pin, u8 cond_op, u32 cond_val, u32 interval) {
    for (int i = 0; i < MAX_MONITORS; i++) {
        if (!monitors[i].active) {
            monitors[i].type = type;
            monitors[i].pin = pin;
            monitors[i].condition_op = cond_op;
            monitors[i].condition_val = cond_val;
            monitors[i].interval_ticks = interval > 0 ? interval : 100000;
            monitors[i].ticks_remaining = monitors[i].interval_ticks;
            monitors[i].trigger_count = 0;
            monitors[i].last_value = 0;
            monitors[i].fired = 0;
            monitors[i].active = 1;
            uart_print("[MON] registered monitor ");
            uart_dec(i);
            uart_print(" type=");
            uart_dec(type);
            uart_print("\n");
            return i;
        }
    }
    return -1;  /* no slot */
}

void monitor_stop_all(void) {
    for (int i = 0; i < MAX_MONITORS; i++) {
        monitors[i].active = 0;
        monitors[i].fired = 0;
        monitors[i].trigger_count = 0;
    }
    uart_print("[MON] all monitors stopped\n");
}

/* ── EVNT Frame ── */
/*
 * Unsolicited event frame: edge → hub
 * "EVNT" + len(4 LE) + JSON payload
 */
void net_send_event(const char *data, int len) {
    net_putc('E'); net_putc('V'); net_putc('N'); net_putc('T');
    net_putc(len & 0xFF);
    net_putc((len >> 8) & 0xFF);
    net_putc((len >> 16) & 0xFF);
    net_putc((len >> 24) & 0xFF);
    net_write((const u8 *)data, len);
}

void monitor_fire_event(int slot, u32 value) {
    /* Build JSON: {"monitor":<slot>,"type":<type>,"value":<value>,"pin":<pin>} */
    char evt[128];
    int l = 0;
    l = buf_str(evt, l, "{\"monitor\":");
    l = buf_dec(evt, l, (u32)slot);
    l = buf_str(evt, l, ",\"type\":");
    l = buf_dec(evt, l, monitors[slot].type);
    l = buf_str(evt, l, ",\"value\":");
    l = buf_dec(evt, l, value);
    if (monitors[slot].type == MON_TYPE_GPIO) {
        l = buf_str(evt, l, ",\"pin\":");
        l = buf_dec(evt, l, monitors[slot].pin);
    }
    l = buf_str(evt, l, ",\"triggers\":");
    l = buf_dec(evt, l, monitors[slot].trigger_count);
    evt[l++] = '}';

    uart_print("[MON] EVNT slot=");
    uart_dec(slot);
    uart_print(" val=");
    uart_dec(value);
    uart_print("\n");

    net_send_event(evt, l);
}

void monitor_tick(void) {
    monitor_poll_counter++;

    for (int i = 0; i < MAX_MONITORS; i++) {
        monitor_t *m = &monitors[i];
        if (!m->active) continue;

        m->ticks_remaining--;
        if (m->ticks_remaining > 0) continue;
        m->ticks_remaining = m->interval_ticks;

        /* Sample the value based on monitor type */
        u32 val = 0;
        if (m->type == MON_TYPE_GPIO) {
            val = gpio_get(m->pin);
        } else if (m->type == MON_TYPE_TEMP) {
            val = virt_temp;
        }
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
            m->fired = 1;
            monitor_fire_event(i, val);
        } else if (!triggered) {
            m->fired = 0;  /* reset when condition clears */
        }
    }
}

/* ── Code Execution ── */
#define CODE_BUF_SIZE 4096
static u8 code_buf[CODE_BUF_SIZE] __attribute__((aligned(4096)));

#define RESULT_BUF_SIZE 256
static char result_buf[RESULT_BUF_SIZE];
static int result_len = 0;

/* Forward declarations */
void play_tone(u16 freq, u16 duration_ms);

/* ── Network Protocol ── */
/*
 * Simple serial protocol:
 *   Request:  "POKE" + len(4 LE) + payload
 *   Response: "RESP" + len(4 LE) + payload
 *   Event:    "EVNT" + len(4 LE) + JSON payload (unsolicited)
 *
 *   Payload types:
 *     "EXEC" + code_bytes            → execute, return x0
 *     "PING"                         → return "PONG"
 *     "INFO"                         → return device info JSON
 *     "TONE" + freq(2) + dur(2)      → play tone
 *     "GPOS" + pin(1) + value(4 LE)  → set GPIO pin
 *     "GPIO"                         → read all GPIO pins as JSON
 *     "TEMP"                         → return temperature JSON
 *     "EMON" + type(1) + pin(1) + op(1) + threshold(4 LE) + interval(4 LE)
 *                                    → register event monitor
 *     "ESTP"                         → stop all monitors
 */

#define NET_BUF_SIZE 8192
static u8 net_buf[NET_BUF_SIZE];
static int net_buf_len = 0;

void net_send_response(const char *data, int len) {
    net_putc('R'); net_putc('E'); net_putc('S'); net_putc('P');
    net_putc(len & 0xFF);
    net_putc((len >> 8) & 0xFF);
    net_putc((len >> 16) & 0xFF);
    net_putc((len >> 24) & 0xFF);
    net_write((const u8 *)data, len);
}

void handle_net_packet(u8 *payload, int len) {
    /* PING */
    if (len >= 4 && payload[0]=='P' && payload[1]=='I' && payload[2]=='N' && payload[3]=='G') {
        uart_print("[NET] PING -> PONG\n");
        net_send_response("PONG", 4);
        return;
    }

    /* INFO — enhanced with commands, sensors, events */
    if (len >= 4 && payload[0]=='I' && payload[1]=='N' && payload[2]=='F' && payload[3]=='O') {
        uart_print("[NET] INFO\n");
        char info[512];
        int l = 0;
        l = buf_str(info, l, "{\"arch\":\"aarch64\",\"version\":\"0.3\",\"status\":\"alive\"");
        l = buf_str(info, l, ",\"caps\":[\"compute\",\"audio\",\"gpio\",\"temp\",\"events\"]");
        l = buf_str(info, l, ",\"commands\":[\"PING\",\"INFO\",\"EXEC\",\"TONE\",\"GPOS\",\"GPIO\",\"TEMP\",\"EMON\",\"ESTP\"]");
        l = buf_str(info, l, ",\"sensors\":{\"temp\":");
        l = buf_dec(info, l, virt_temp);
        l = buf_str(info, l, ",\"gpio_pins\":");
        l = buf_dec(info, l, GPIO_PINS);
        l = buf_str(info, l, "}");
        l = buf_str(info, l, ",\"events\":true");
        /* Active monitors */
        l = buf_str(info, l, ",\"monitors\":[");
        int first = 1;
        for (int i = 0; i < MAX_MONITORS; i++) {
            if (!monitors[i].active) continue;
            if (!first) info[l++] = ',';
            first = 0;
            info[l++] = '{';
            l = buf_str(info, l, "\"id\":");
            l = buf_dec(info, l, (u32)i);
            l = buf_str(info, l, ",\"type\":");
            l = buf_dec(info, l, monitors[i].type);
            l = buf_str(info, l, ",\"val\":");
            l = buf_dec(info, l, monitors[i].last_value);
            l = buf_str(info, l, ",\"fired\":");
            info[l++] = monitors[i].fired ? '1' : '0';
            l = buf_str(info, l, ",\"triggers\":");
            l = buf_dec(info, l, monitors[i].trigger_count);
            info[l++] = '}';
        }
        info[l++] = ']';
        info[l++] = '}';
        net_send_response(info, l);
        return;
    }

    /* EXEC */
    if (len >= 4 && payload[0]=='E' && payload[1]=='X' && payload[2]=='E' && payload[3]=='C') {
        int code_len = len - 4;
        if (code_len > CODE_BUF_SIZE) code_len = CODE_BUF_SIZE;
        mem_copy(code_buf, payload + 4, code_len);

        uart_print("[NET] EXEC ");
        uart_dec(code_len);
        uart_print(" bytes\n");

        /* Execute ARM64 code — return value in x0 */
        mem_set(result_buf, 0, RESULT_BUF_SIZE);
        result_len = 0;

        u64 (*fn)(char *, int *) = (u64 (*)(char *, int *))code_buf;
        u64 ret = fn(result_buf, &result_len);

        uart_print("[NET] x0=");
        uart_dec(ret);
        uart_print("\n");

        /* Build response */
        char resp[128];
        mem_set(resp, 0, 128);
        int rlen = 0;

        if (result_len > 0) {
            mem_copy(resp, result_buf, result_len);
            rlen = result_len;
        } else {
            /* Return x0 as decimal string */
            const char *p = "x0=";
            while (*p) resp[rlen++] = *p++;
            char dbuf[20]; int di = 0;
            u64 v = ret;
            if (v == 0) dbuf[di++] = '0';
            else { while (v > 0) { dbuf[di++] = '0' + (v % 10); v /= 10; } }
            while (di > 0) resp[rlen++] = dbuf[--di];
            resp[rlen++] = '\n';
        }

        net_send_response(resp, rlen);
        return;
    }

    /* TONE */
    if (len >= 4 && payload[0]=='T' && payload[1]=='O' && payload[2]=='N' && payload[3]=='E') {
        if (len >= 8) {
            u16 freq = payload[4] | (payload[5] << 8);
            u16 dur_ms = payload[6] | (payload[7] << 8);
            uart_print("[AUDIO] tone ");
            uart_dec(freq);
            uart_print("Hz ");
            uart_dec(dur_ms);
            uart_print("ms\n");
            play_tone(freq, dur_ms);
            net_send_response("OK", 2);
        }
        return;
    }

    /* GPOS — set GPIO pin value */
    /* Format: "GPOS" + pin(1) + value(4 LE) = 9 bytes */
    if (len >= 9 && payload[0]=='G' && payload[1]=='P' && payload[2]=='O' && payload[3]=='S') {
        u8 pin = payload[4];
        u32 value = payload[5] | (payload[6] << 8) | (payload[7] << 16) | (payload[8] << 24);
        if (pin < GPIO_PINS) {
            gpio_set(pin, value);
            uart_print("[GPIO] pin ");
            uart_dec(pin);
            uart_print(" = ");
            uart_dec(value);
            uart_print("\n");
            /* Respond with confirmation JSON */
            char resp[64];
            int rl = 0;
            rl = buf_str(resp, rl, "{\"pin\":");
            rl = buf_dec(resp, rl, pin);
            rl = buf_str(resp, rl, ",\"value\":");
            rl = buf_dec(resp, rl, value);
            resp[rl++] = '}';
            net_send_response(resp, rl);
        } else {
            net_send_response("ERR:pin", 7);
        }
        return;
    }

    /* GPIO — read all pin states as JSON */
    if (len >= 4 && payload[0]=='G' && payload[1]=='P' && payload[2]=='I' && payload[3]=='O') {
        uart_print("[GPIO] read all\n");
        char resp[256];
        int rl = 0;
        rl = buf_str(resp, rl, "{\"pins\":[");
        for (int i = 0; i < GPIO_PINS; i++) {
            if (i > 0) resp[rl++] = ',';
            rl = buf_dec(resp, rl, gpio_pins[i]);
        }
        rl = buf_str(resp, rl, "]}");
        net_send_response(resp, rl);
        return;
    }

    /* TEMP — read virtual temperature */
    if (len >= 4 && payload[0]=='T' && payload[1]=='E' && payload[2]=='M' && payload[3]=='P') {
        uart_print("[TEMP] read ");
        uart_dec(virt_temp);
        uart_print("C\n");
        char resp[64];
        int rl = 0;
        rl = buf_str(resp, rl, "{\"temp\":");
        rl = buf_dec(resp, rl, virt_temp);
        rl = buf_str(resp, rl, ",\"unit\":\"C\"}");
        net_send_response(resp, rl);
        return;
    }

    /* EMON — register event monitor */
    /* Format: "EMON" + type(1) + pin(1) + op(1) + threshold(4 LE) + interval(4 LE) = 15 bytes */
    if (len >= 15 && payload[0]=='E' && payload[1]=='M' && payload[2]=='O' && payload[3]=='N') {
        u8  type      = payload[4];
        u8  pin       = payload[5];
        u8  op        = payload[6];
        u32 threshold = payload[7] | (payload[8] << 8) | (payload[9] << 16) | (payload[10] << 24);
        u32 interval  = payload[11] | (payload[12] << 8) | (payload[13] << 16) | (payload[14] << 24);

        uart_print("[MON] EMON type=");
        uart_dec(type);
        uart_print(" pin=");
        uart_dec(pin);
        uart_print(" op=");
        uart_dec(op);
        uart_print(" thresh=");
        uart_dec(threshold);
        uart_print("\n");

        int slot = monitor_register(type, pin, op, threshold, interval);
        char resp[64];
        int rl = 0;
        if (slot >= 0) {
            rl = buf_str(resp, rl, "{\"monitor\":");
            rl = buf_dec(resp, rl, (u32)slot);
            rl = buf_str(resp, rl, ",\"status\":\"ok\"}");
        } else {
            rl = buf_str(resp, rl, "{\"error\":\"no_slot\"}");
        }
        net_send_response(resp, rl);
        return;
    }

    /* ESTP — stop all monitors */
    if (len >= 4 && payload[0]=='E' && payload[1]=='S' && payload[2]=='T' && payload[3]=='P') {
        uart_print("[MON] ESTOP\n");
        monitor_stop_all();
        net_send_response("{\"monitors\":\"stopped\"}", 21);
        return;
    }

    uart_print("[NET] unknown command\n");
    net_send_response("ERR", 3);
}

void poll_network(void) {
    /* Read available bytes into buffer */
    while (net_available() && net_buf_len < NET_BUF_SIZE) {
        net_buf[net_buf_len++] = net_getc();
    }

    /* Check for complete packet: "POKE" + len(4) + payload */
    while (net_buf_len >= 8) {
        if (net_buf[0]=='P' && net_buf[1]=='O' && net_buf[2]=='K' && net_buf[3]=='E') {
            u32 plen = net_buf[4] | (net_buf[5]<<8) | (net_buf[6]<<16) | (net_buf[7]<<24);
            if (plen > NET_BUF_SIZE - 8) { net_buf_len = 0; break; } /* too big */
            if (net_buf_len >= (int)(8 + plen)) {
                /* Complete packet */
                handle_net_packet(net_buf + 8, plen);
                /* Shift buffer */
                int consumed = 8 + plen;
                int left = net_buf_len - consumed;
                if (left > 0) {
                    for (int i = 0; i < left; i++)
                        net_buf[i] = net_buf[consumed + i];
                }
                net_buf_len = left;
            } else {
                break; /* need more data */
            }
        } else {
            /* Sync lost — skip one byte */
            for (int i = 0; i < net_buf_len - 1; i++)
                net_buf[i] = net_buf[i + 1];
            net_buf_len--;
        }
    }
}

/* ── Audio (simple square wave via UART2 or PWM) ── */
/*
 * QEMU virt doesn't have a real audio device easily.
 * We simulate audio by generating PCM data and sending
 * it out via a 3rd serial port mapped to a pipe/file.
 * The host can play it with aplay/sox.
 *
 * For now: generate tone data and write to UART2.
 * QEMU: -serial ... -serial ... -serial file:/tmp/audio.pcm
 */

#define UART2_BASE 0x09050000
#define UART2_DR   (*(volatile u32 *)(UART2_BASE + 0x00))
#define UART2_FR   (*(volatile u32 *)(UART2_BASE + 0x18))

void audio_putc(u8 val) {
    while (UART2_FR & (1 << 5));
    UART2_DR = val;
}

void play_tone(u16 freq, u16 duration_ms) {
    /* Generate 8-bit unsigned PCM, 8000 Hz sample rate */
    u32 samples = (8000 * duration_ms) / 1000;
    u32 period = 8000 / (freq ? freq : 440);
    u32 half = period / 2;

    for (u32 i = 0; i < samples; i++) {
        u8 val = ((i % period) < half) ? 200 : 56; /* square wave */
        audio_putc(val);
    }
    uart_print("[AUDIO] done\n");
}

/* ── Shell ── */
#define CMD_BUF_SIZE 256
static char cmd_buf[CMD_BUF_SIZE];
static int cmd_len = 0;

void shell_prompt(void) {
    uart_print("poke-arm> ");
}

void shell_exec(void) {
    cmd_buf[cmd_len] = 0;

    if (str_eq(cmd_buf, "help")) {
        uart_print("\ncommands: help, info, test, tone, net, gpio, temp, mon\n");
    } else if (str_eq(cmd_buf, "info")) {
        uart_print("\nPOKE OS ARM64 v0.3\n");
        uart_print("arch: aarch64 | platform: QEMU virt\n");
        uart_print("UART0: console | UART1: network | UART2: audio\n");
        uart_print("GPIO: ");
        uart_dec(GPIO_PINS);
        uart_print(" virtual pins | temp: ");
        uart_dec(virt_temp);
        uart_print("C\n");
        uart_print("monitors: ");
        int active = 0;
        for (int i = 0; i < MAX_MONITORS; i++)
            if (monitors[i].active) active++;
        uart_dec(active);
        uart_print("/");
        uart_dec(MAX_MONITORS);
        uart_print(" active\n");
    } else if (str_eq(cmd_buf, "test")) {
        uart_print("\n2+2=");
        /* ARM64: mov x0, #2; add x0, x0, #2; ret */
        code_buf[0]=0x40; code_buf[1]=0x00; code_buf[2]=0x80; code_buf[3]=0xD2;
        code_buf[4]=0x00; code_buf[5]=0x08; code_buf[6]=0x00; code_buf[7]=0x91;
        code_buf[8]=0xC0; code_buf[9]=0x03; code_buf[10]=0x5F; code_buf[11]=0xD6;
        u64 (*fn)(void) = (u64 (*)(void))code_buf;
        u64 r = fn();
        uart_dec(r);
        uart_print("\n");
    } else if (str_eq(cmd_buf, "tone")) {
        uart_print("\nPlaying 440Hz 500ms...\n");
        play_tone(440, 500);
    } else if (str_eq(cmd_buf, "net")) {
        uart_print("\nSending PING on UART1...\n");
        net_print("HELLO FROM POKE ARM\n");
    } else if (str_eq(cmd_buf, "gpio")) {
        uart_print("\nGPIO pins:\n");
        for (int i = 0; i < GPIO_PINS; i++) {
            uart_print("  pin[");
            uart_dec(i);
            uart_print("] = ");
            uart_dec(gpio_pins[i]);
            uart_print("\n");
        }
    } else if (str_eq(cmd_buf, "temp")) {
        uart_print("\ntemperature: ");
        uart_dec(virt_temp);
        uart_print("C\n");
    } else if (str_eq(cmd_buf, "mon")) {
        uart_print("\nmonitors:\n");
        for (int i = 0; i < MAX_MONITORS; i++) {
            if (!monitors[i].active) continue;
            uart_print("  [");
            uart_dec(i);
            uart_print("] type=");
            uart_dec(monitors[i].type);
            uart_print(" val=");
            uart_dec(monitors[i].last_value);
            uart_print(" triggers=");
            uart_dec(monitors[i].trigger_count);
            uart_print(" fired=");
            uart_dec(monitors[i].fired);
            uart_print("\n");
        }
    } else if (cmd_len > 0) {
        uart_print("\nunknown: ");
        uart_print(cmd_buf);
        uart_print("\n");
    }

    cmd_len = 0;
    shell_prompt();
}

/* ── Kernel Main ── */
void kernel_main(void) {
    uart_print("\n");
    uart_print("  ____   ___  _  _______ \n");
    uart_print(" |  _ \\ / _ \\| |/ / ____|\n");
    uart_print(" | |_) | | | | ' /|  _|  \n");
    uart_print(" |  __/| |_| | . \\| |___ \n");
    uart_print(" |_|    \\___/|_|\\_\\_____|\n");
    uart_print("\n");
    uart_print("  POKE OS ARM64 v0.3\n");
    uart_print("  arch: aarch64\n");
    uart_print("  UART0: console\n");
    uart_print("  UART1: network (serial<->TCP)\n");
    uart_print("  UART2: audio out\n");
    uart_print("  GPIO: 11 virtual pins\n");
    uart_print("  TEMP: virtual sensor\n");
    uart_print("  EVNT: event monitor system\n");
    uart_print("\n");

    gpio_init();
    monitor_init();
    net_init();
    uart_print("[GPIO] initialized (11 pins)\n");
    uart_print("[TEMP] initialized (22C)\n");
    uart_print("[MON] initialized (4 slots)\n");
    uart_print("[NET] UART1 initialized\n");

    shell_prompt();

    while (1) {
        /* Poll console */
        char c = uart_getc();
        if (c) {
            if (c == '\r' || c == '\n') {
                uart_print("\n");
                shell_exec();
            } else if (c == 127 || c == '\b') {
                if (cmd_len > 0) { cmd_len--; uart_print("\b \b"); }
            } else if (cmd_len < CMD_BUF_SIZE - 1) {
                cmd_buf[cmd_len++] = c;
                uart_putc(c);
            }
        }

        /* Poll network */
        poll_network();

        /* Tick virtual sensors + monitors */
        temp_tick_update();
        monitor_tick();
    }
}
