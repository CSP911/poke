"""A finger: three servo joints on a fixed palm, like an index finger.

The palm lies flat (top surface at z = 0, extending toward -x). The finger
starts straight, pointing +x from the palm's edge. Curling it toward the
palm needs NEGATIVE joint angles (the joint axis is +y), but the robot is
not told that — a person watching has to teach it.
"""
import mujoco

SEG = (0.045, 0.030, 0.024)          # proximal, middle, distal (m)
W, H = 0.018, 0.016                  # finger width / thickness
KP, TORQUE, LIMIT = 2.0, 0.6, 1.75

def finger_xml():
    def seg(i):
        L = SEG[i]
        return (f'<geom name="g{i}" type="capsule" fromto="0 0 0 {L} 0 0" size="{H/2}" '
                f'rgba="0.95 0.78 0.66 1" mass="0.02"/>')
    tip = f'<site name="tip" pos="{SEG[2]} 0 0" size="0.004"/>'
    inner = ""
    for i in reversed(range(3)):
        pos = "0 0 0" if i == 0 else f"{SEG[i-1]} 0 0"
        inner = (f'<body name="p{i}" pos="{pos}"><joint name="j{i}" type="hinge" axis="0 1 0" '
                 f'range="{-LIMIT} {LIMIT}" damping="0.01" armature="0.0005"/>'
                 f'{seg(i)}{tip if i == 2 else ""}{inner}</body>')
    acts = "".join(f'<position joint="j{i}" kp="{KP}" forcerange="{-TORQUE} {TORQUE}" ctrlrange="{-LIMIT} {LIMIT}"/>'
                   for i in range(3))
    return f"""<mujoco>
  <compiler angle="radian"/>
  <option timestep="0.002" gravity="0 0 -9.81"/>
  <visual><global offwidth="1280" offheight="720"/></visual>
  <worldbody>
    <light pos="0.05 -0.3 0.6" dir="0 0.4 -1"/>
    <geom name="table" type="plane" pos="0 0 -0.03" size="0.4 0.4 0.01" rgba="0.82 0.84 0.8 1"/>
    <geom name="palm" type="box" pos="-0.05 0 -0.012" size="0.05 0.03 0.012" rgba="0.9 0.72 0.6 1"/>
    <body name="base" pos="0 0 {H/2 + 0.002}">
      {inner}
    </body>
  </worldbody>
  <actuator>{acts}</actuator>
</mujoco>"""

def model():
    return mujoco.MjModel.from_xml_string(finger_xml())

def tip_palm_gap(m, d):
    """Distance from the fingertip down to the palm's top surface, over the palm
    (ground truth, used only by the simulated watcher in --auto mode)."""
    tx, ty, tz = d.site_xpos[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, "tip")]
    over = -0.10 <= tx <= 0.0
    return (tz - 0.0) if over else 0.2 + abs(tx)

def tip_touches_palm(m, d):
    palm = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, "palm")
    for i in range(d.ncon):
        c = d.contact[i]
        if palm in (c.geom1, c.geom2):
            other = c.geom2 if c.geom1 == palm else c.geom1
            if other in (mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, "g2"),
                         mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, "g1")):
                return True
    return False

BODY = {
    "njoints": 3, "segments": 3,
    "joints": [{"joint": 0, "axis": "pitch", "where": "knuckle (base)"},
               {"joint": 1, "axis": "pitch", "where": "middle"},
               {"joint": 2, "axis": "pitch", "where": "nearest the tip"}],
    "summary": "a finger: 3 servo joints in a chain, all bending in the same plane",
    "notes": ("A finger fixed at its base to a palm, like an index finger. It starts straight. "
              "Which SIGN of the joint angles curls it toward the palm is NOT known to you. "
              "There is no camera: a person watches and comments by voice, and their comments "
              "tune your params between attempts. Each attempt starts from the straight finger "
              "(all joints 0); reach the requested pose within about 2 s, then hold it."),
}
