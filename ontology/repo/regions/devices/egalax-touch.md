---
id: egalax-touch
name: "eGalax USB touch (0eef:0005)"
kind: device
one_liner: "Full-speed HID touch on the VL805 hub: 11-byte reports, X 0..1023, Y 0..599, ~130 Hz"
injected_by: tools/ontology.py
parent: devices
---
# eGalax touch controller
VID 0x0eef PID 0x0005, full speed, EP0 max packet 64 (despite full speed — learn it from the first
8 descriptor bytes), interrupt IN 0x82, 11-byte reports: [1] bit0 tip, [4..5] X LE 0..1023,
[6..7] Y LE 0..599 in panel pixels. Never SET_IDLE: it STALLs and wedges the hub's single TT.
Behind the VL805's internal hub 2109:3431 (4 ports, single TT → child TT port number 0).
