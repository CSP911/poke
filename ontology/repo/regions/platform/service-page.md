---
id: service-page
name: "Resident service page"
kind: protocol
one_liner: "How the hub asks a resident for something: a 4 KB page, req_seq/rsp_seq, op + bytes"
injected_by: tools/ontology.py
parent: platform
---
# Resident service page
The kernel maps one 4 KB page RW into every resident at `UNIT_SVC_VA` (0x100000190000). Layout
(`svc_page_t` in poke_api.h): `req_seq, op, req_len, req[1536]` then `rsp_seq, status, rsp_len,
rsp[1536]`. The hub sends `SREQ op data`; the kernel copies it in, bumps `req_seq`, and keeps
resuming the resident until `rsp_seq == req_seq` (5 s limit). The resident answers in its TICK:
fill `rsp`, `rsp_len`, `status`, `dsb sy`, then `rsp_seq = req_seq` last.

Op numbering is the resident's own. Known: sdcard 1 info / 2 read(lba, n≤2) / 3 write(lba+512 B);
i2c 1 scan / 2 read(addr, reg, n) / 3 write(addr, bytes); AI-written sensors 1 → JSON reading.
