"""The first scene, in simulation: "a body it has never met".

  1. a body is assembled (its shape is hidden from the robot)
  2. self-discovery: wiggle each joint, learn the body from the IMU
  3. skill generation: the language model writes a C skill for this body
     and the intent; the numbers are tuned on the body by parallel trials
  4. a module is removed → discover again → generate again → tune again

Usage: python scene.py [axes] [intent]     e.g.  python scene.py pppp
"""
import json, os, subprocess, sys, time
import sim, tune, discover

ROUNDS = 3
INTENT = "crawl toward the light, which is straight ahead of the head (+x), as fast as you can"

def generate(body, intent, tag, feedback=None):
    os.makedirs(os.path.join(sim.HERE, "build"), exist_ok=True)
    bpath = os.path.join(sim.BUILD, f"body-{tag}.json")
    with open(bpath, "w") as f: json.dump(body, f)
    src = os.path.join(sim.HERE, "skills", f"gen-{tag}.c")
    cmd = ["node", os.path.join(sim.HERE, "generate.js"), bpath, intent, src] + ([feedback] if feedback else [])
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"generation failed: {r.stderr.strip()}")
    meta = json.loads(r.stdout.strip().splitlines()[-1])
    return src, meta, time.time() - t0

def bring_up(axes, intent, tag):
    print(f"\n[인식] 몸 조립: 관절 {len(axes)}개 (모양은 로봇에게 숨김)")
    print("[자기 발견] 관절을 하나씩 흔들어 보는 중")
    body = discover.discover(axes, log=lambda s: print("   " + s.strip()))
    print(f"   → {body['summary']}")
    print(f"[기술 생성] 의도: {intent}")
    best_run, feedback = None, None
    for rnd in range(1, ROUNDS + 1):
        try:
            src, meta, dt = generate(body, intent, f"{tag}{rnd}", feedback)
        except RuntimeError as e:
            print(f"   라운드 {rnd} ✗ {str(e)[:120]} — 건너뜀")
            continue
        print(f"   라운드 {rnd} → '{meta['name']}' ({dt:.0f}s): {meta.get('idea','')}")
        try:
            lib = sim.compile_skill(src, f"gen-{tag}{rnd}")
        except RuntimeError as e:
            print(f"     ✗ 컴파일 실패: {e.splitlines()[0][:120]}")
            feedback = f"It did not compile: {e[:600]}"
            continue
        ranges = [(float(p["lo"]), float(p["hi"])) for p in meta["params"]]
        base = sim.run(axes, lib, params=tuple((lo + hi) / 2 for lo, hi in ranges), seconds=10)
        best, _, _ = tune.tune(axes, lib, ranges, seconds=6, pop=48, gens=6, log=lambda s: None)
        res = sim.run(axes, lib, params=tuple(best), seconds=10)
        ev = res["safety"]; sat = (ev["rate_limited"] + ev["clamped"]) / (10 * sim.HZ * len(axes)) * 100
        print(f"     생성 직후 {base['forward']*100:+.1f} cm → 다듬은 뒤 {res['forward']*100:+.1f} cm / 10 s, "
              f"안전 개입 {sat:.1f}%")
        run = {"meta": meta, "best": best, "res": res, "base": base, "src": src}
        if best_run is None or sim.score(res, 10, len(axes)) > sim.score(best_run["res"], 10, len(axes)):
            best_run = run
        feedback = (f"Your skill '{meta['name']}' ({meta.get('idea','')}) after tuning its params on the body "
                    f"moved {res['forward']*100:+.1f} cm forward in 10 s (sideways {res['lateral']*100:+.1f} cm). "
                    f"Best params: {dict(zip([p['name'] for p in meta['params']], [round(float(v),3) for v in best]))}. "
                    f"Safety layer intervened on {sat:.1f}% of joint-ticks (keep this low). "
                    f"Rethink the movement STRUCTURE to travel farther toward +x; do not just change numbers.")
    meta, best, res, base = best_run["meta"], best_run["best"], best_run["res"], best_run["base"]
    print(f"   ★ 채택: '{meta['name']}' {res['forward']*100:+.1f} cm / 10 s ({res['speed_cm_s']:.2f} cm/s)")
    return {"axes": "".join(axes), "skill": meta["name"], "idea": meta.get("idea", ""),
            "generated_cm": base["forward"] * 100, "tuned_cm": res["forward"] * 100,
            "params": dict(zip([p["name"] for p in meta["params"]], [round(float(v), 3) for v in best]))}

if __name__ == "__main__":
    axes = list(sys.argv[1]) if len(sys.argv) > 1 else list("pppp")
    intent = sys.argv[2] if len(sys.argv) > 2 else INTENT
    out = [bring_up(axes, intent, "a")]
    print("\n[분리] 모듈 하나를 뽑는다 (꼬리 관절)")
    out.append(bring_up(axes[:-1], intent, "b"))
    with open(os.path.join(sim.BUILD, "scene-result.json"), "w") as f: json.dump(out, f, indent=2, ensure_ascii=False)
    print("\n요약")
    for o in out:
        print(f"  {o['axes']:<6} {o['skill']:<28} 생성 직후 {o['generated_cm']:+6.1f} cm → 다듬은 뒤 {o['tuned_cm']:+6.1f} cm / 10 s")
