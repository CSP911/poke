/* Single-precision math for skills on POKE. No libc, no tables, no errno.
 * Range reduction + short polynomials; errors ~1e-6 relative over normal
 * control ranges, which is far below servo resolution. */
#include "math.h"

typedef unsigned int u32;
static float bits2f(u32 b) { union { u32 u; float f; } v = { b }; return v.f; }
static u32 f2bits(float f) { union { float f; u32 u; } v = { f }; return v.u; }

float fabsf(float x) { return __builtin_fabsf(x); }
float sqrtf(float x) { return __builtin_sqrtf(x); }
float floorf(float x) { return __builtin_floorf(x); }
float ceilf(float x) { return __builtin_ceilf(x); }
float roundf(float x) { return __builtin_roundf(x); }
float truncf(float x) { return __builtin_truncf(x); }
float fminf(float a, float b) { return a < b ? a : b; }
float fmaxf(float a, float b) { return a > b ? a : b; }
float copysignf(float a, float b) { return __builtin_copysignf(a, b); }
float fmodf(float x, float y) { if (y == 0) return NAN; return x - truncf(x / y) * y; }

/* sin/cos on [-pi/4, pi/4] */
static float ksin(float x) { float z = x * x; return x * (1 + z * (-1.6666667e-1f + z * (8.3333310e-3f + z * (-1.9840874e-4f + z * 2.7525562e-6f)))); }
static float kcos(float x) { float z = x * x; return 1 + z * (-0.5f + z * (4.1666638e-2f + z * (-1.3888397e-3f + z * 2.4390449e-5f))); }

static int reduce(float x, float *r) {      /* x = k*pi/2 + r, |r| <= pi/4 */
    float k = roundf(x * 0.63661977f);
    *r = (x - k * 1.5707963f) - k * 7.5497894e-8f;
    return ((int)k) & 3;
}
float sinf(float x) {
    float r; int q = reduce(x, &r);
    switch (q) { case 0: return ksin(r); case 1: return kcos(r); case 2: return -ksin(r); default: return -kcos(r); }
}
float cosf(float x) {
    float r; int q = reduce(x, &r);
    switch (q) { case 0: return kcos(r); case 1: return -ksin(r); case 2: return -kcos(r); default: return ksin(r); }
}
float tanf(float x) { return sinf(x) / cosf(x); }

float atanf(float x) {
    float s = x < 0 ? -1.0f : 1.0f, a = fabsf(x), off = 0;
    int inv = a > 1.0f;
    if (inv) a = 1.0f / a;
    if (a > 0.41421356f) { off = (float)M_PI_4; a = (a - 1) / (a + 1); }
    float z = a * a;
    float r = off + a * (1 + z * (-3.3333145e-1f + z * (1.9993551e-1f + z * (-1.4208899e-1f + z * (1.0656264e-1f + z * -7.5289640e-2f)))));
    if (inv) r = (float)M_PI_2 - r;
    return s * r;
}
float atan2f(float y, float x) {
    if (x > 0) return atanf(y / x);
    if (x < 0) return y >= 0 ? atanf(y / x) + (float)M_PI : atanf(y / x) - (float)M_PI;
    return y > 0 ? (float)M_PI_2 : (y < 0 ? -(float)M_PI_2 : 0.0f);
}
float asinf(float x) { if (x > 1 || x < -1) return NAN; return atan2f(x, sqrtf((1 - x) * (1 + x))); }
float acosf(float x) { if (x > 1 || x < -1) return NAN; return atan2f(sqrtf((1 - x) * (1 + x)), x); }

float expf(float x) {
    if (x > 88.7f) return INFINITY;
    if (x < -103.9f) return 0.0f;
    float k = roundf(x * 1.44269504f);
    float r = (x - k * 0.693145752f) - k * 1.42860677e-6f;
    float p = 1 + r * (1 + r * (0.5f + r * (1.6666667e-1f + r * (4.1666667e-2f + r * (8.3333333e-3f + r * 1.3888889e-3f)))));
    int ki = (int)k;
    if (ki < -126) { p *= bits2f((u32)(1) << 23); ki += 126; }   /* stay normal */
    return p * bits2f((u32)(ki + 127) << 23);
}
float logf(float x) {
    if (x < 0) return NAN;
    if (x == 0) return -INFINITY;
    if (isinf(x)) return x;
    u32 b = f2bits(x);
    int e = (int)((b >> 23) & 0xff) - 127;
    if (e == -127) { x *= 8388608.0f; b = f2bits(x); e = (int)((b >> 23) & 0xff) - 150; }
    float m = bits2f((b & 0x7fffff) | 0x3f800000);              /* [1,2) */
    if (m > 1.41421356f) { m *= 0.5f; e++; }
    float s = (m - 1) / (m + 1), z = s * s;
    float l = 2 * s * (1 + z * (3.3333333e-1f + z * (2.0000000e-1f + z * (1.4285715e-1f + z * 1.1111111e-1f))));
    return l + (float)e * 0.69314718f;
}
float log2f(float x) { return logf(x) * 1.44269504f; }
float log10f(float x) { return logf(x) * 0.43429448f; }
float powf(float x, float y) {
    if (y == 0) return 1;
    if (x == 0) return y > 0 ? 0 : INFINITY;
    if (x < 0) {
        float yi = truncf(y);
        if (yi != y) return NAN;
        float r = expf(y * logf(-x));
        return (((int)yi) & 1) ? -r : r;
    }
    return expf(y * logf(x));
}
float sinhf(float x) { float e = expf(x); return 0.5f * (e - 1 / e); }
float coshf(float x) { float e = expf(x); return 0.5f * (e + 1 / e); }
float tanhf(float x) {
    if (x > 9) return 1; if (x < -9) return -1;
    float e = expf(2 * x); return (e - 1) / (e + 1);
}
float hypotf(float a, float b) { return sqrtf(a * a + b * b); }

/* double names → float code (control precision) */
double fabs(double x) { return __builtin_fabs(x); }
double sqrt(double x) { return __builtin_sqrt(x); }
double floor(double x) { return __builtin_floor(x); }
double ceil(double x) { return __builtin_ceil(x); }
double round(double x) { return __builtin_round(x); }
double trunc(double x) { return __builtin_trunc(x); }
double fmin(double a, double b) { return a < b ? a : b; }
double fmax(double a, double b) { return a > b ? a : b; }
double fmod(double x, double y) { return fmodf((float)x, (float)y); }
double copysign(double a, double b) { return __builtin_copysign(a, b); }
double sin(double x) { return sinf((float)x); }
double cos(double x) { return cosf((float)x); }
double tan(double x) { return tanf((float)x); }
double asin(double x) { return asinf((float)x); }
double acos(double x) { return acosf((float)x); }
double atan(double x) { return atanf((float)x); }
double atan2(double y, double x) { return atan2f((float)y, (float)x); }
double exp(double x) { return expf((float)x); }
double log(double x) { return logf((float)x); }
double log2(double x) { return log2f((float)x); }
double log10(double x) { return log10f((float)x); }
double pow(double x, double y) { return powf((float)x, (float)y); }
double sinh(double x) { return sinhf((float)x); }
double cosh(double x) { return coshf((float)x); }
double tanh(double x) { return tanhf((float)x); }
double hypot(double a, double b) { return hypotf((float)a, (float)b); }
