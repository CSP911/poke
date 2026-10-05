"""Check the POKE motion path before coaching on it.

1. Same skill, same body: run it on the Mac (native) and on POKE (EL0 unit,
   kernel safety filter) and compare the targets tick by tick.
2. Broken skills — NaN output, an infinite loop, a wild pointer — must be
   contained: the device keeps answering, the skill is discarded.
"""
import os, sys, time, math
import numpy as np
import mujoco
import sim, finger, pokeedge

HOST = os.environ.get("POKE_HOST", "twin")       # 'twin' boots the QEMU twin; or an IP / tcp://host:port
SK = os.path.join(sim.HERE, "skills")

CURL = r'''
#include "motion_io.h"
#include <math.h>
void skill_step(motion_io_t *io) {
    float s = io->param[0] < 0 ? -1.0f : 1.0f, amp = io->param[1] > 0 ? io->param[1] : 1.2f;
    float e = io->t / 2.0f; if (e > 1) e = 1;
    e = 0.5f - 0.5f * cosf((float)M_PI * e);                 /* smooth 0 → 1 */
    for (int j = 0; j < io->njoints; j++) io->cmd[j] = s * amp * e * (1.0f - 0.15f * j) + 0.05f * sinf(6.0f * io->t + j);
}'''
BROKEN = {
    "nan":     '#include "motion_io.h"\nvoid skill_step(motion_io_t *io){ for(int j=0;j<io->njoints;j++) io->cmd[j]=0.0f/0.0f; }',
    "hang":    '#include "motion_io.h"\nvoid skill_step(motion_io_t *io){ volatile int x=0; if(io->tick>5) for(;;) x++; for(int j=0;j<io->njoints;j++) io->cmd[j]=0.1f; }',
    "pointer": '#include "motion_io.h"\nvoid skill_step(motion_io_t *io){ if(io->tick>5) *(volatile int*)0x80000=1; for(int j=0;j<io->njoints;j++) io->cmd[j]=0.1f; }',
    "huge":    '#include "motion_io.h"\nvoid skill_step(motion_io_t *io){ for(int j=0;j<io->njoints;j++) io->cmd[j]=(io->tick%2)?50.0f:-50.0f; }',
}

def write(name, code):
    p = os.path.join(SK, f"test-{name}.c"); open(p, "w").write(code); return p

def run_finger(stepper, seconds=3.0):
    m = finger.model(); d = mujoco.MjData(m); sub = int(round(1.0 / sim.HZ / m.opt.timestep))
    out = []
    for k in range(int(seconds * sim.HZ)):
        tgt = stepper(k / sim.HZ, k, d.qpos[:3].tolist())
        d.ctrl[:3] = tgt
        for _ in range(sub):
            mujoco.mj_step(m, d)
        out.append(list(tgt))
    return np.array(out), d.qpos[:3].copy()

def main():
    src = write("curl", CURL)
    params = [-1.0, 1.3]
    # Mac reference
    lib = sim.compile_skill(src, "test-curl")
    import ctypes
    L = ctypes.CDLL(lib); L.skill_step.argtypes = [ctypes.POINTER(sim.MotionIO)]
    io = sim.MotionIO(); io.njoints = 3
    for j in range(3): io.limit[j] = finger.LIMIT
    io.param[0], io.param[1] = params
    saf = None
    def mac(t, k, q):
        nonlocal saf
        if saf is None: saf = sim.Safety(3, finger.LIMIT); saf.prev = np.array(q)
        io.t, io.tick = t, k
        for j in range(3): io.q[j] = q[j]
        L.skill_step(ctypes.byref(io))
        return saf.filter([io.cmd[j] for j in range(3)], 0, 1 / sim.HZ)
    ref, q_ref = run_finger(mac)

    twin = pokeedge.Twin() if HOST == "twin" else None
    host = twin.__enter__() if twin else HOST
    print(f"edge: {'QEMU twin at ' + host if twin else host}")
    edge = pokeedge.PokeSkill(host)
    img = pokeedge.build(src, "test-curl")
    print(edge.load(img, [finger.LIMIT] * 3, [0, 0, 0]))
    edge.attempt(params)
    lat = []
    def poke(t, k, q):
        t0 = time.time(); cmd, ev = edge.step(t, k, q); lat.append((time.time() - t0) * 1000, ); poke.ev = ev
        return cmd
    got, q_got = run_finger(poke)
    diff = np.abs(ref - got).max()
    print(f"[1] same skill, Mac vs POKE: max target difference {diff:.2e} rad over {len(ref)} ticks; "
          f"final pose Mac {np.degrees(q_ref).round()} / POKE {np.degrees(q_got).round()}")
    print(f"    round trip per tick: median {np.median(lat):.2f} ms, p95 {np.percentile(lat, 95):.2f} ms; "
          f"skill step on the Pi {poke.ev['step_us']} µs; safety {poke.ev}")
    ok = diff < 1e-4

    for name, code in BROKEN.items():
        img = pokeedge.build(write(name, code), f"test-{name}")
        edge.load(img, [finger.LIMIT] * 3, [0, 0, 0]); edge.attempt([0])
        result = "ran to the end"
        try:
            for k in range(40):
                cmd, ev = edge.step(k / 100, k, [0.0, 0.0, 0.0])
            result = f"ran; kernel filtered it — last cmd {np.round(cmd, 3)}, safety {ev}"
        except pokeedge.EdgeError as e:
            result = f"stopped by the kernel: {e}"
        alive = edge.request(b"PING").decode()
        print(f"[2] broken skill '{name}': {result}\n    device: {alive}")
        ok = ok and alive.startswith("PONG")
    edge.stop()
    if twin:
        twin.__exit__(None, None, None)
    print("\nPASS" if ok else "\nFAIL")

if __name__ == "__main__":
    main()
