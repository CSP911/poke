---
id: syscalls
name: "Unit syscalls"
kind: protocol
one_liner: "The ten primitives an EL0 unit may call; everything else is compiled into the unit"
injected_by: tools/ontology.py
parent: platform
---
# Unit syscalls (x8 = number, SVC #0)
0 log(str) · 1 exit(code) · 2 yield · 3 sleep(ms) → returns 1 when the kernel wants a resident to
stop · 4 mbox(buf, len) VideoCore mailbox on the unit's behalf (≤128 B) · 5 touch(x, y, tip)
publish an input sample · 6 wait — park until the kernel resumes the unit on purpose (motion step) ·
10–23 persona api_t slots (fb_clear, fb_rect, fb_text, now_ms, …).

Conveniences are not syscalls: they live in headers compiled into the volatile binary
(`skill_crt.c`, `i2c_bus.h`, `sdhci.h`). A unit never maps hardware it was not given.
