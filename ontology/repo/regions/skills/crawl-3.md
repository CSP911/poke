---
id: crawl-3
name: "Crawl, 3-joint chain"
kind: skill
one_liner: "inchworm-loop-crawl: Drive a phase-lagged traveling wave down the pitch joints using an asymmetric slow-stance/fast-recovery waveform (not a plain sine) so each segment drags the bo"
injected_by: tools/ontology.py
parent: skills
---
# Crawl, 3-joint chain
        **Body:** a chain of four segments and three pitch joints lying on the ground

        **Idea:** Drive a phase-lagged traveling wave down the pitch joints using an asymmetric slow-stance/fast-recovery waveform (not a plain sine) so each segment drags the body forward during the long slow phase and lifts/snaps back with reduced ground friction during the short fast phase, producing net forward creep like a looping inchworm.

        **Params:** freq_hz [0.3, 2.5], amplitude [0.1, 0.6], phase_lag [0.0, 1.0], duty [0.1, 0.9], bias [-0.3, 0.3], gradient [0.0, 1.0]

        ```c
        // SKILL-META {"name":"inchworm-loop-crawl","idea":"Drive a phase-lagged traveling wave down the pitch joints using an asymmetric slow-stance/fast-recovery waveform (not a plain sine) so each segment drags the body forward during the long slow phase and lifts/snaps back with reduced ground friction during the short fast phase, producing net forward creep like a looping inchworm.","params":[{"name":"freq_hz","lo":0.3,"hi":2.5},{"name":"amplitude","lo":0.1,"hi":0.6},{"name":"phase_lag","lo":0.0,"hi":1.0},{"name":"duty","lo":0.1,"hi":0.9},{"name":"bias","lo":-0.3,"hi":0.3},{"name":"gradient","lo":0.0,"hi":1.0}]}

#include "motion_io.h"
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846f
#endif

/* returns p mapped from the provided raw param (0 => mid of [lo,hi]) */
static float get_param(motion_io_t *io, int idx, float lo, float hi) {
    float v = io->param[idx];
    if (v == 0.0f) {
        return 0.5f * (lo + hi);
    }
    return v;
}

/* smoothstep 0..1 -> 0..1 with zero derivative at both ends */
static float smooth01(float u) {
    if (u < 0.0f) u = 0.0f;
    if (u > 1.0f) u = 1.0f;
    return 0.5f - 0.5f * cosf(M_PI * u);
}

/* periodic phase p in [0,1): returns 0..1 waveform with asymmetric
 * timing controlled by duty: slow smooth rise over [0,duty], then a
 * (usually shorter) smooth fall over the remainder. The asymmetric
 * dwell time creates different effective angular speeds in the two
 * halves of the cycle, which (combined with ground friction) biases
 * net motion of the body. */
static float wave(float p, float duty) {
    if (duty < 0.02f) duty = 0.02f;
    if (duty > 0.98f) duty = 0.98f;

    /* wrap p into [0,1) */
    p = p - floorf(p);

    if (p < duty) {
        float u = p / duty;
        return smooth01(u);          /* 0 -> 1 */
    } else {
        float u = (p - duty) / (1.0f - duty);
        return 1.0f - smooth01(u);   /* 1 -> 0 */
    }
}

void skill_step(motion_io_t *io) {
    float freq      = get_param(io, 0, 0.3f, 2.5f);
    float amplitude = get_param(io, 1, 0.1f, 0.6f);
    float phase_lag = get_param(io, 2, 0.0f, 1.0f);
    float duty      = get_param(io, 3, 0.1f, 0.9f);
    float bias      = get_param(io, 4, -0.3f, 0.3f);
    float gradient  = get_param(io, 5, 0.0f, 1.0f);

    int n = io->njoints;
    if (n > MOTION_MAX_JOINTS) n = MOTION_MAX_JOINTS;

    for (int j = 0; j < n; j++) {
        /* only pitch joints get the crawling wave; yaw joints (none on
         * this body) are left centered to avoid veering sideways */
        if (io->axis[j] == 1) {
            io->cmd[j] = 0.0f;
            continue;
        }

        float denom = (n > 1) ? (float)(n - 1) : 1.0f;
        float grow  = 1.0f + gradient * ((float)j / denom); /* tail amplifies */
        float amp_j = amplitude * grow;

        /* traveling wave: later joints (toward tail) lag in phase */
        float p = freq * io->t - (float)j * phase_lag;
        float w = wave(p, duty); /* 0..1 */

        float ang = bias + amp_j * (2.0f * w - 1.0f);

        /* respect the body's own joint limit as a soft clamp too */
        float lim = io->limit[j];
        if (lim > 0.0f) {
            if (ang > lim) ang = lim;
            if (ang < -lim) ang = -lim;
        }

        io->cmd[j] = ang;
    }
}

        ```
