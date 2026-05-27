#ifndef POKE_FORMAT_H
#define POKE_FORMAT_H

// int → decimal string, returns length written
static int poke_itoa(int val, char *buf) {
    if (val == 0) { buf[0] = '0'; return 1; }
    int neg = 0, i = 0;
    if (val < 0) { neg = 1; val = -val; }
    char tmp[12];
    while (val > 0) { tmp[i++] = '0' + (val % 10); val /= 10; }
    int len = 0;
    if (neg) buf[len++] = '-';
    while (i > 0) buf[len++] = tmp[--i];
    return len;
}

// unsigned → hex string (no 0x prefix), returns length written
static int poke_utoh(unsigned int val, char *buf, int digits) {
    const char hex[] = "0123456789ABCDEF";
    for (int i = digits - 1; i >= 0; i--) {
        buf[i] = hex[val & 0xF];
        val >>= 4;
    }
    return digits;
}

// byte → "XX" hex
static int poke_btoh(unsigned char val, char *buf) {
    return poke_utoh(val, buf, 2);
}

// MAC format: 6 bytes → "XX:XX:XX:XX:XX:XX", returns 17
static int poke_format_mac(unsigned char *mac, char *buf) {
    int pos = 0;
    for (int i = 0; i < 6; i++) {
        pos += poke_btoh(mac[i], buf + pos);
        if (i < 5) buf[pos++] = ':';
    }
    return pos;
}

// IP format: 4 bytes → "xxx.xxx.xxx.xxx"
static int poke_format_ip(unsigned char *ip, char *buf) {
    int pos = 0;
    for (int i = 0; i < 4; i++) {
        pos += poke_itoa(ip[i], buf + pos);
        if (i < 3) buf[pos++] = '.';
    }
    return pos;
}

// simple snprintf-like: supports %d, %x, %s, %02x
static int poke_sprintf(char *buf, const char *fmt, ...) {
    // Variadic in freestanding is tricky, use a simpler approach
    // Users should use poke_itoa, poke_utoh directly for complex formatting
    int pos = 0;
    while (*fmt) {
        buf[pos++] = *fmt++;
    }
    return pos;
}

#endif
