"""Generative-body simulator.

A body is a chain of servo modules (joints) with rigid segments between them.
Skills are C programs speaking the motion ABI in motion_io.h; the simulator
plays the kernel's part: it fills motion_io_t, calls skill_step(), and sends
the requested targets through the same safety rules the edge kernel will
enforce (angle limit, rate limit, emergency stop).
"""
import ctypes, os, subprocess, math
import numpy as np
import mujoco

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
MAXJ = 16
HZ = 100

# ── body ────────────────────────────────────────────────────────────────
SEG_LEN, SEG_W, SEG_H, SEG_MASS = 0.08, 0.06, 0.04, 0.06   # a small servo module
SERVO_TORQUE = 0.25                                       # N·m, hobby-servo class
SERVO_KP = 3.0

def body_xml(axes, limit=1.2):
    """axes: list of 'p' (pitch) / 'y' (yaw) per joint, head first."""
    hl = SEG_LEN / 2
    def seg(i):
        return (f'<geom type="box" size="{hl*0.95} {SEG_W/2} {SEG_H/2}" pos="{-hl} 0 0" '
                f'mass="{SEG_MASS}" rgba="0.25 0.35 0.9 1" friction="1.0 0.02 0.002"/>')
    xml = seg(0)
    inner = ""
    for i in reversed(range(len(axes))):
        ax = "0 1 0" if axes[i] == "p" else "0 0 1"
        inner = (f'<body name="s{i+1}" pos="{-SEG_LEN} 0 0">'
                 f'<joint name="j{i}" type="hinge" axis="{ax}" range="{-limit} {limit}" damping="0.02" armature="0.002"/>'
                 f'{seg(i+1)}{inner}</body>')
    acts = "".join(f'<position joint="j{i}" kp="{SERVO_KP}" forcerange="{-SERVO_TORQUE} {SERVO_TORQUE}" ctrlrange="{-limit} {limit}"/>'
                   for i in range(len(axes)))
    return f"""<mujoco>
  <compiler angle="radian"/>
  <option timestep="0.002" integrator="implicitfast"/>
  <worldbody>
    <light pos="0 0 3"/>
    <geom name="floor" type="plane" size="5 5 0.1" friction="1.0 0.02 0.002" rgba="0.9 0.9 0.88 1"/>
    <body name="s0" pos="0 0 {SEG_H/2+0.005}">
      <freejoint/>
      <site name="imu" pos="{-hl} 0 0"/>
      {xml}{inner}
    </body>
  </worldbody>
  <actuator>{acts}</actuator>
  <sensor><framequat objtype="site" objname="imu"/><accelerometer site="imu"/></sensor>
</mujoco>"""

# ── motion ABI (mirror of motion_io.h) ──────────────────────────────────
class MotionIO(ctypes.Structure):
    _fields_ = [("njoints", ctypes.c_int),
                ("axis", ctypes.c_int * MAXJ),
                ("limit", ctypes.c_float * MAXJ),
                ("t", ctypes.c_float),
                ("tick", ctypes.c_ulong),
                ("q", ctypes.c_float * MAXJ),
                ("imu", ctypes.c_float * 6),
                ("param", ctypes.c_float * 8),
                ("cmd", ctypes.c_float * MAXJ)]

def compile_skill(src, name=None):
    os.makedirs(BUILD, exist_ok=True)
    name = name or os.path.splitext(os.path.basename(src))[0]
    out = os.path.join(BUILD, f"{name}.dylib")
    r = subprocess.run(["cc", "-O2", "-shared", "-fPIC", "-I", HERE, "-o", out, src, "-lm"],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return out

# ── safety layer: the rules the edge kernel will enforce ────────────────
class Safety:
    """Every requested target passes here. A skill can ask for anything;
    the body only ever receives what these rules allow."""
    def __init__(self, n, limit, max_rate=6.0, max_tilt=math.radians(80)):
        self.limit, self.max_rate, self.max_tilt = limit, max_rate, max_tilt
        self.prev = np.zeros(n)
        self.estop = False
        self.events = {"clamped": 0, "rate_limited": 0, "nonfinite": 0, "estop": None}

    def filter(self, cmd, tilt, dt):
        out = np.array(cmd, dtype=float)
        bad = ~np.isfinite(out)
        if bad.any():
            self.events["nonfinite"] += int(bad.sum()); out[bad] = self.prev[bad]
        if abs(tilt) > self.max_tilt and not self.estop:
            self.estop = True; self.events["estop"] = f"tilt {math.degrees(tilt):.0f} deg"
        if self.estop:
            return self.prev.copy()                       # freeze where we are
        c = np.clip(out, -self.limit, self.limit)
        self.events["clamped"] += int((c != out).sum())
        step = self.max_rate * dt
        r = np.clip(c, self.prev - step, self.prev + step)
        self.events["rate_limited"] += int((np.abs(r - c) > 1e-9).sum())
        self.prev = r
        return r

def quat_to_rpy(w, x, y, z):
    roll = math.atan2(2*(w*x + y*z), 1 - 2*(x*x + y*y))
    pitch = math.asin(max(-1.0, min(1.0, 2*(w*y - z*x))))
    yaw = math.atan2(2*(w*z + x*y), 1 - 2*(y*y + z*z))
    return roll, pitch, yaw

# ── run one skill on one body ───────────────────────────────────────────
def run(axes, lib_path, params=(), seconds=8.0, limit=1.2, record=False):
    model = mujoco.MjModel.from_xml_string(body_xml(axes, limit))
    data = mujoco.MjData(model)
    lib = ctypes.CDLL(lib_path)
    lib.skill_step.argtypes = [ctypes.POINTER(MotionIO)]
    io = MotionIO()
    n = len(axes)
    io.njoints = n
    for i, a in enumerate(axes):
        io.axis[i] = 0 if a == "p" else 1
        io.limit[i] = limit
    for i, v in enumerate(params[:8]):
        io.param[i] = v
    safety = Safety(n, limit)
    sub = int(round(1.0 / HZ / model.opt.timestep))
    mujoco.mj_forward(model, data)
    start = data.subtree_com[1].copy()
    trace = []
    for k in range(int(seconds * HZ)):
        io.t = k / HZ; io.tick = k
        for j in range(n):
            io.q[j] = data.qpos[7 + j]
        w, x, y, z = data.sensordata[0:4]
        r, p, yw = quat_to_rpy(w, x, y, z)
        io.imu[0], io.imu[1], io.imu[2] = r, p, yw
        io.imu[3], io.imu[4], io.imu[5] = data.sensordata[4:7]
        lib.skill_step(ctypes.byref(io))
        tilt = max(abs(r), abs(p))
        data.ctrl[:n] = safety.filter([io.cmd[j] for j in range(n)], tilt, 1.0 / HZ)
        for _ in range(sub):
            mujoco.mj_step(model, data)
        if record and k % 5 == 0:
            trace.append(data.subtree_com[1].copy())
    end = data.subtree_com[1].copy()
    d = end - start
    return {"forward": float(d[0]), "lateral": float(d[1]),
            "speed_cm_s": float(d[0] / seconds * 100),
            "safety": safety.events, "trace": np.array(trace) if record else None}

def score(res, seconds=None, njoints=None):
    """distance toward the light (+x); sideways drift, an e-stop, and asking
    for motion the body cannot do (rate-limit / clamp hits) all cost.
    A skill that only works because the safety layer shaves its demands
    would heat and wear real servos (docs/design/motion-safety.md, rule 5)."""
    s = res["forward"] - 0.3 * abs(res["lateral"])
    ev = res["safety"]
    if seconds and njoints:
        sat = (ev["rate_limited"] + ev["clamped"]) / (seconds * HZ * njoints)   # fraction of joint-ticks
        s -= 0.2 * max(0.0, sat - 0.02)                                         # free up to 2 %
    return s - (1.0 if ev["estop"] else 0.0)

if __name__ == "__main__":
    lib = compile_skill(os.path.join(HERE, "skills", "wave.c"))
    for axes in (["p"] * 4, ["p"] * 3):
        for d in (1, -1):
            r = run(axes, lib, params=(0.6, 1.0, 1.0, d))
            print(f"{len(axes)} pitch joints dir={d:+d}: forward {r['forward']*100:+.1f} cm "
                  f"({r['speed_cm_s']:+.2f} cm/s) lateral {r['lateral']*100:+.1f} cm  safety {r['safety']}")
