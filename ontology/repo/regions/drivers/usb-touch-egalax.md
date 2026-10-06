---
id: usb-touch-egalax
name: "usb-touch-egalax"
kind: driver
one_liner: "Pi 4 bare-metal USB host: PCIe root complex + VL805 xHCI + VL805 internal USB 2."
injected_by: tools/ontology.py
parent: drivers
---
# usb-touch-egalax
    Pi 4 bare-metal USB host: PCIe root complex + VL805 xHCI + VL805 internal USB 2.0 hub + eGalax (0eef:0005) full-speed HID touch on a hub port. Fills api->svc->touch with panel-pixel coordinates. Hot-plug: the hub ports are polled every 500 ms; unplug releases the slot and the touch service, replug re-enumerates.

    **Provides:** touch · **inject:** `poke resident usb-touch-egalax` · **source:** `usb_touch.c`

    ## Capability windows
    - `0xFD500000` len `0x10000` device — PCIe root complex (BCM2711 host bridge registers)
- `0x600000000` len `0x200000` device — PCIe outbound window → VL805 BAR0 (xHCI MMIO)
- `0x02200000` len `0x30000` nc — xHCI DMA region: DCBAA, rings, contexts, scratchpad

## Hardware notes
- **panel**: Waveshare 7" HDMI LCD 1024x600, capacitive USB touch
- **touch_controller**: {"vid": "0x0eef", "pid": "0x0005", "vendor": "D-WAV/eGalax", "speed": "full", "ep0_mps": 64, "interrupt_in": "0x82", "report_bytes": 11, "report_hz": 130}
- **hub**: {"vid": "0x2109", "pid": "0x3431", "vendor": "VIA VL805 internal USB 2.0 hub", "ports": 4, "tt": "single", "ttt": 3}
- **cabling**: touch USB must go into a USB-A jack (all four are behind the VL805 hub); the USB-C port is a different controller (DWC2) and is not served here
