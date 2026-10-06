---
id: skills
name: "Motion skills"
kind: topic
one_liner: "Generated movement programs: the ABI they speak, the safety they run under, and the ones that worked"
injected_by: tools/ontology.py
role: representative
use_when: "how to write a skill for a body (the motion ABI) · which skills worked on a 3-joint finger or a servo chain, with their tuned params · what the safety filter clamps · how skills are tuned from a person's voice · how a skill is stored and restored"
---
# Motion skills
A skill is a volatile C program generated for one body and one intent. It speaks motion_io.h,
runs as an EL0 unit stepped by `MSTP`, and only ever writes joint targets into its own page; the
kernel's safety filter decides what reaches a motor. Skills that a person approved ("그거야") are
remembered on the device's SD with their tuned numbers.
