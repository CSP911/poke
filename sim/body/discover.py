"""Self-discovery: the body does not know its own shape. It wiggles one joint
at a time and watches the head IMU. A joint that tilts the head up/down is a
pitch joint; one that turns it sideways is a yaw joint. Joints farther from
the head move it less. The result is the body description a skill generator
is given — learned, not configured.

Only motion_io_t-level signals are used (commanded targets, measured joint
angles, head IMU), the same ones a real POKE body has."""
import json, math, os, sys
import numpy as np
import mujoco
import sim

WIGGLE_HZ, WIGGLE_AMP, SECONDS = 1.0, 0.5, 2.0

def probe_joint(axes, j, limit=1.2):
    """Oscillate joint j only; return head roll/pitch/yaw swing and joint tracking."""
    model = mujoco.MjModel.from_xml_string(sim.body_xml(axes, limit))
    data = mujoco.MjData(model)
    sub = int(round(1.0 / sim.HZ / model.opt.timestep))
    safety = sim.Safety(len(axes), limit)
    rpy, track = [], []
    for k in range(int(SECONDS * sim.HZ)):
        t = k / sim.HZ
        cmd = np.zeros(len(axes)); cmd[j] = WIGGLE_AMP * math.sin(2 * math.pi * WIGGLE_HZ * t)
        w, x, y, z = data.sensordata[0:4]
        r, p, yw = sim.quat_to_rpy(w, x, y, z)
        data.ctrl[:len(axes)] = safety.filter(cmd, max(abs(r), abs(p)), 1.0 / sim.HZ)
        for _ in range(sub):
            mujoco.mj_step(model, data)
        rpy.append((r, p, yw)); track.append((cmd[j], data.qpos[7 + j]))
    rpy = np.array(rpy); track = np.array(track)
    swing = rpy.max(0) - rpy.min(0)                    # peak-to-peak per axis
    follows = float(np.corrcoef(track[:, 0], track[:, 1])[0, 1])
    return swing, follows

def discover(axes, log=print):
    """`axes` builds the simulated body; the discovery itself never reads it."""
    n = len(axes)
    joints = []
    for j in range(n):
        swing, follows = probe_joint(axes, j)
        roll, pitch, yaw = (math.degrees(v) for v in swing)
        kind = "pitch" if pitch >= yaw else "yaw"
        joints.append({"joint": j, "axis": kind, "head_pitch_deg": round(pitch, 1),
                       "head_yaw_deg": round(yaw, 1), "responds": follows > 0.8})
        log(f"  joint {j}: head pitch swing {pitch:5.1f}°, yaw swing {yaw:5.1f}° → {kind}"
            f"{'' if follows > 0.8 else '  (NOT RESPONDING)'}")
    body = {"njoints": n, "segments": n + 1, "segment_length_m": sim.SEG_LEN,
            "joints": joints,
            "summary": f"a chain of {n+1} segments joined by {n} servo joints, head first; "
                       + ", ".join(f"joint {x['joint']} bends {'up/down' if x['axis']=='pitch' else 'sideways'}" for x in joints)}
    return body

if __name__ == "__main__":
    for axes in (["p", "p", "p", "p"], ["p", "y", "p", "y"], ["p", "p", "p"]):
        print(f"simulated body (hidden from discovery): {''.join(axes)}")
        b = discover(axes)
        found = "".join("p" if x["axis"] == "pitch" else "y" for x in b["joints"])
        print(f"  discovered: {found}  {'✓' if found == ''.join(axes) else '✗'}\n")
