/* Minimal <math.h> for skills on POKE (no libc on the edge).
 * Single precision, accurate to ~1e-6 over the ranges control code uses.
 * Double-precision names are provided too and route through the float code. */
#ifndef POKE_SKILL_MATH_H
#define POKE_SKILL_MATH_H

#define M_PI     3.14159265358979323846
#define M_PI_2   1.57079632679489661923
#define M_PI_4   0.78539816339744830962
#define M_E      2.7182818284590452354
#define M_SQRT2  1.41421356237309504880
#define INFINITY (__builtin_inff())
#define NAN      (__builtin_nanf(""))
#define isnan(x)    __builtin_isnan(x)
#define isinf(x)    __builtin_isinf(x)
#define isfinite(x) __builtin_isfinite(x)

float fabsf(float); float sqrtf(float); float floorf(float); float ceilf(float);
float roundf(float); float truncf(float); float fminf(float, float); float fmaxf(float, float);
float fmodf(float, float); float copysignf(float, float);
float sinf(float); float cosf(float); float tanf(float);
float asinf(float); float acosf(float); float atanf(float); float atan2f(float, float);
float expf(float); float logf(float); float log2f(float); float log10f(float); float powf(float, float);
float sinhf(float); float coshf(float); float tanhf(float); float hypotf(float, float);

double fabs(double); double sqrt(double); double floor(double); double ceil(double);
double round(double); double trunc(double); double fmin(double, double); double fmax(double, double);
double fmod(double, double); double copysign(double, double);
double sin(double); double cos(double); double tan(double);
double asin(double); double acos(double); double atan(double); double atan2(double, double);
double exp(double); double log(double); double log2(double); double log10(double); double pow(double, double);
double sinh(double); double cosh(double); double tanh(double); double hypot(double, double);

#endif
