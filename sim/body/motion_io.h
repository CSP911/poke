/* ============================================
 * POKE motion ABI — shared by the simulator and (later) the edge kernel.
 *
 * A skill is a generated, volatile program that moves a body. It never
 * touches motors directly: each control tick the kernel fills a motion_io_t,
 * calls skill_step(), then passes io->cmd through the safety layer
 * (angle limits, rate limits, emergency stop) before anything moves.
 *
 *   void skill_step(motion_io_t *io)      called at MOTION_HZ
 *
 * Field order is ABI: append only.
 * ============================================ */
#ifndef POKE_MOTION_IO_H
#define POKE_MOTION_IO_H

#define MOTION_MAX_JOINTS 16
#define MOTION_HZ 100

typedef struct {
    /* body, filled once by discovery and kept by the kernel */
    int   njoints;                         /* joints in the chain, head first        */
    int   axis[MOTION_MAX_JOINTS];         /* 0 = pitch (up/down), 1 = yaw (sideways) */
    float limit[MOTION_MAX_JOINTS];        /* |angle| the safety layer allows (rad)  */

    /* state, refreshed every tick */
    float t;                               /* seconds since the skill started        */
    unsigned long tick;
    float q[MOTION_MAX_JOINTS];            /* measured joint angles (rad)            */
    float imu[6];                          /* head: roll, pitch, yaw (rad), ax, ay, az */
    float param[8];                        /* tunable numbers (hub sets / tuner sets) */

    /* output */
    float cmd[MOTION_MAX_JOINTS];          /* requested joint targets (rad)          */
} motion_io_t;

void skill_step(motion_io_t *io);

#endif
