#ifndef POKE_STRING_H
#define POKE_STRING_H

typedef unsigned int u32;

static int poke_strlen(const char *s) {
    int n = 0; while (*s++) n++; return n;
}

static void *poke_memcpy(void *dst, const void *src, int n) {
    char *d = (char *)dst; const char *s = (const char *)src;
    while (n--) *d++ = *s++;
    return dst;
}

static void *poke_memset(void *dst, int val, int n) {
    char *d = (char *)dst;
    while (n--) *d++ = (char)val;
    return dst;
}

static int poke_strcmp(const char *a, const char *b) {
    while (*a && *b) { if (*a != *b) return *a - *b; a++; b++; }
    return *a - *b;
}

static char *poke_strcpy(char *dst, const char *src) {
    char *d = dst;
    while ((*d++ = *src++));
    return dst;
}

static char *poke_strcat(char *dst, const char *src) {
    char *d = dst;
    while (*d) d++;
    while ((*d++ = *src++));
    return dst;
}

#endif
