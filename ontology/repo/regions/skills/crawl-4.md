---
id: crawl-4
name: "Crawl, 4-joint chain"
kind: skill
one_liner: "inchworm-asym-wave: A phase-skewed traveling pitch wave runs tail-to-head with a slow power-stroke and a fast recovery-stroke (like an inchworm/looper caterpillar), so dry-friction"
injected_by: tools/ontology.py
parent: skills
---
# Crawl, 4-joint chain
        **Body:** a chain of five segments and four pitch joints lying on the ground, head first (+x)

        **Idea:** A phase-skewed traveling pitch wave runs tail-to-head with a slow power-stroke and a fast recovery-stroke (like an inchworm/looper caterpillar), so dry-friction asymmetry between the two strokes converts the vertical ripple into net forward creep.

        **Params:** freq_hz [0.3, 2.0], amplitude_rad [0.2, 0.9], phase_lag_rad [0.3, 3.0], bias_rad [-0.3, 0.3], skew_k [-0.9, 0.9], head_gain [0.2, 1.5]

        ```c
        // SKILL-META {"name":"inchworm-asym-wave","idea":"A phase-skewed traveling pitch wave runs tail-to-head with a slow power-stroke and a fast recovery-stroke (like an inchworm/looper caterpillar), so dry-friction asymmetry between the two strokes converts the vertical ripple into net forward creep.","params":[{"name":"freq_hz","lo":0.3,"hi":2.0},{"name":"amplitude_rad","lo":0.2,"hi":0.9},{"name":"phase_lag_rad","lo":0.3,"hi":3.0},{"name":"bias_rad","lo":-0.3,"hi":0.3},{"name":"skew_k","lo":-0.9,"hi":0.9},{"name":"head_gain","lo":0.2,"hi":1.5}]}
#include "motion_io.h"
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846f
#endif

#define PARAM_OR(idx, lo, hi) (io->param[idx] != 0.0f ? io->param[idx] : 0.5f*((lo)+(hi)))

void skill_step(motion_io_t *io)
{
    float freq_hz      = PARAM_OR(0, 0.3f, 2.0f);
    float amplitude    = PARAM_OR(1, 0.2f, 0.9f);
    float phase_lag    = PARAM_OR(2, 0.3f, 3.0f);
    float bias         = PARAM_OR(3, -0.3f, 0.3f);
    float skew_k       = PARAM_OR(4, -0.9f, 0.9f);
    float head_gain    = PARAM_OR(5, 0.2f, 1.5f);

    /* clamp skew to keep phase-warp monotonic (avoid folding) */
    if (skew_k > 0.95f) skew_k = 0.95f;
    if (skew_k < -0.95f) skew_k = -0.95f;

    int n = io->njoints;
    if (n < 2) n = 2; /* avoid div by zero below */

    for (int j = 0; j < io->njoints; j++) {
        /* traveling wave: each joint lags the previous one, wave runs
         * tail -> head over time (power stroke), creating a hump that
         * sweeps forward along the body. */
        float lag = j * phase_lag;
        float phase = 2.0f * M_PI * freq_hz * io->t - lag;

        /* asymmetric phase warp: spends more time near one extreme
         * (slow, high-friction power stroke) and snaps quickly through
         * the other (fast, lifted recovery stroke). This breaks the
         * time-reversal symmetry of a plain sine wave so dry friction
         * yields net forward progress even with pure pitch joints. */
        float psi = phase + skew_k * sinf(phase);

        /* amplitude gradient: tail segments (higher j) sweep more,
         * head stays comparatively steadier, acting like a guide. */
        float gain = head_gain + (1.0f - head_gain) * ((float)j / (float)(n - 1));
        float amp = amplitude * gain;

        float target = bias + amp * sinf(psi);

        /* keep a safety margin under the hard limit */
        float lim = io->limit[j] * 0.95f;
        if (target > lim) target = lim;
        if (target < -lim) target = -lim;

        io->cmd[j] = target;
    }
}

        ```
