---
id: finger-fist
name: "Finger fist (3 pitch joints)"
kind: skill
one_liner: "finger-curl-fist: Ease all three finger joints together toward the palm with a smooth S-curve; direction is taken as a pure sign (so tuning magnitude can't accidentally flip or w"
injected_by: tools/ontology.py
parent: skills
---
# Finger fist (3 pitch joints)
        **Body:** a finger of three servo joints on a fixed palm; negative angles curl toward the palm

        **Idea:** Ease all three finger joints together toward the palm with a smooth S-curve; direction is taken as a pure sign (so tuning magnitude can't accidentally flip or weaken direction) while each joint's amplitude scales independently, holding the final curled pose — built from the best-praised attempt (dir defaulted negative, more curl requested) with the direction decoupled from amplitude to fix the sign confusion seen in later attempts.

        **Params:** dir [-1.0, 1.0], base_amp [0.0, 2.2], mid_amp [0.0, 2.2], tip_amp [0.0, 2.2], duration [1.0, 3.0]

        ```c
        // SKILL-META {"name":"finger-curl-fist","idea":"Ease all three finger joints together toward the palm with a smooth S-curve; direction is taken as a pure sign (so tuning magnitude can't accidentally flip or weaken direction) while each joint's amplitude scales independently, holding the final curled pose — built from the best-praised attempt (dir defaulted negative, more curl requested) with the direction decoupled from amplitude to fix the sign confusion seen in later attempts.","params":[{"name":"dir","lo":-1.0,"hi":1.0},{"name":"base_amp","lo":0.0,"hi":2.2},{"name":"mid_amp","lo":0.0,"hi":2.2},{"name":"tip_amp","lo":0.0,"hi":2.2},{"name":"duration","lo":1.0,"hi":3.0}]}

#include "motion_io.h"
#include <math.h>

static float clampf(float v, float lo, float hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

void skill_step(motion_io_t *io) {
    /* The one attempt that got positive feedback ("moved", "curl a bit more")
       used a default negative direction. Several later attempts used small
       positive or small negative *scaled* dir values and were all called
       "opposite" — because dir was being multiplied directly into the
       target, so its magnitude (not just its sign) changed the curl amount
       and made the motion look wrong/weak. Here dir only supplies a SIGN;
       the amplitude parameters alone control how much each joint curls. */

    float dir_raw  = io->param[0]; /* -1..1, sign only */
    float base_amp = io->param[1] != 0.0f ? io->param[1] : 1.3f;
    float mid_amp  = io->param[2] != 0.0f ? io->param[2] : 1.3f;
    float tip_amp  = io->param[3] != 0.0f ? io->param[3] : 1.3f;
    float duration = io->param[4] != 0.0f ? io->param[4] : 2.0f;

    float dir = (dir_raw < 0.0f) ? -1.0f : (dir_raw > 0.0f ? 1.0f : -1.0f);

    if (duration < 0.1f) duration = 0.1f;

    float s = clampf(io->t / duration, 0.0f, 1.0f);
    /* smoothstep ease-in/ease-out for a natural curl */
    float ease = s * s * (3.0f - 2.0f * s);

    float amps[3] = { base_amp, mid_amp, tip_amp };

    for (int j = 0; j < io->njoints && j < 3; j++) {
        float target = dir * amps[j] * ease;
        float lim = io->limit[j];
        if (lim > 0.0f) {
            target = clampf(target, -lim, lim);
        }
        io->cmd[j] = target;
    }

    /* any extra joints beyond the modeled 3, hold straight */
    for (int j = 3; j < io->njoints; j++) {
        io->cmd[j] = 0.0f;
    }
}

        ```
