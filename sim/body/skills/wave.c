/* Baseline skill (hand-written): a travelling sine wave along the chain.
 * param[0] amplitude (rad)  param[1] frequency (Hz)  param[2] phase step per joint (rad)
 * param[3] direction (+1 forward, -1 backward) */
#include <math.h>
#include "../motion_io.h"

void skill_step(motion_io_t *io) {
    float A = io->param[0] > 0 ? io->param[0] : 0.6f;
    float f = io->param[1] > 0 ? io->param[1] : 1.0f;
    float k = io->param[2] != 0 ? io->param[2] : 1.0f;
    float d = io->param[3] < 0 ? -1.0f : 1.0f;
    for (int j = 0; j < io->njoints; j++)
        io->cmd[j] = A * sinf(2.0f * 3.14159265f * f * io->t - d * k * (float)j);
}
