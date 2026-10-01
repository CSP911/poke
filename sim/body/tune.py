"""Tune a skill's numbers on a body by trial: many simulations in parallel,
keep the best, search around it. The structure of the movement comes from
the skill's code; this only finds its amplitude/frequency/phase/... values.
(The simulated counterpart of spreading trials over a POKE fleet.)"""
import os, sys, time, json
import numpy as np
from multiprocessing import Pool
import sim

def _eval(job):
    axes, lib, params, seconds = job
    r = sim.run(axes, lib, params=params, seconds=seconds)
    return sim.score(r, seconds, len(axes)), r["forward"], r["lateral"], r["safety"]["estop"]

def tune(axes, lib, ranges, seconds=6.0, pop=48, gens=6, seed=0, workers=None, log=print):
    """ranges: list of (lo, hi) per param. Returns best params and history."""
    rng = np.random.default_rng(seed)
    lo = np.array([r[0] for r in ranges]); hi = np.array([r[1] for r in ranges])
    cand = lo + rng.random((pop, len(ranges))) * (hi - lo)
    best, best_s, hist = None, -1e9, []
    t0 = time.time()
    with Pool(workers or os.cpu_count()) as p:
        for g in range(gens):
            res = p.map(_eval, [(axes, lib, tuple(c), seconds) for c in cand])
            scores = np.array([r[0] for r in res])
            i = int(scores.argmax())
            if scores[i] > best_s:
                best_s, best = float(scores[i]), cand[i].copy()
                best_fwd = res[i][1]
            hist.append(best_s)
            log(f"  gen {g}: best {best_fwd*100:+.1f} cm in {seconds:.0f}s  ({len(cand)} trials, {time.time()-t0:.1f}s)")
            # next generation: around the best, shrinking
            sigma = (hi - lo) * (0.25 * (0.6 ** g))
            elite = cand[np.argsort(-scores)[:6]]
            cand = np.concatenate([elite, np.clip(best + rng.normal(0, 1, (pop - 6, len(ranges))) * sigma, lo, hi)])
    return best, best_s, hist

if __name__ == "__main__":
    lib = sim.compile_skill(os.path.join(sim.HERE, "skills", "wave.c"))
    ranges = [(0.1, 1.2), (0.2, 3.0), (-3.0, 3.0), (1, 1)]   # A, f, phase step (sign = direction), dir
    for axes in (["p"] * 4, ["p"] * 3):
        print(f"body: {len(axes)} pitch joints")
        best, s, _ = tune(axes, lib, ranges)
        r = sim.run(axes, lib, params=tuple(best), seconds=10)
        print(f"  → A={best[0]:.2f} f={best[1]:.2f}Hz k={best[2]:+.2f}: {r['forward']*100:+.1f} cm / 10 s "
              f"({r['speed_cm_s']:.2f} cm/s), safety {r['safety']}")
