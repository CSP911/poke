---
id: usb-touch-egalax-lesson-8
name: "usb-touch-egalax: lesson 8"
kind: lesson
one_liner: "hot-plug without the hub's interrupt endpoint: GET_STATUS on each hub port every 500 ms over the hub's EP0 (Link-TRB-wrapped ring); Disable Slot on unplug, full child enumeration on replug; endpoint e"
injected_by: tools/ontology.py
parent: usb-touch-egalax
---
# usb-touch-egalax — lesson 8
hot-plug without the hub's interrupt endpoint: GET_STATUS on each hub port every 500 ms over the hub's EP0 (Link-TRB-wrapped ring); Disable Slot on unplug, full child enumeration on replug; endpoint errors only set a flag so an unplug never floods Reset Endpoint commands
