"""
boot.py — v4: absolute mouse WITH a Report ID, coexisting with the keyboard.

ROOT CAUSE (found this session, verified on Windows): v2 (and v3) enabled a
NO-report-ID custom pointer on the SAME HID interface as the keyboard
(`usb_hid.enable((abs_mouse, KEYBOARD))`). Mixing a no-report-ID top-level
collection with another collection is invalid HID. Windows rejects the whole
interface with **Code 10 ("device cannot start")**, so NEITHER mouse nor
keyboard worked. CircuitPython and macOS were lenient enough to enumerate it,
which masked the fault. Proof: abs_mouse enabled ALONE -> interface OK + exact
absolute positioning; abs_mouse + keyboard -> Code 10.

FIX: give the absolute mouse a Report ID (2) so it is a valid second collection
alongside the stock keyboard (Report ID 1). The 1-byte Report ID is prepended by
CircuitPython; the payload stays 5 bytes and code.py is unchanged
(send_report(buf) auto-uses the single report id).

Report payload (after the prepended Report ID byte):
  byte 0: buttons (bit0 left, bit1 right, bit2 middle)
  byte 1-2: X (uint16 LE, 0..32767 absolute)
  byte 3-4: Y (uint16 LE, 0..32767 absolute)
"""

import usb_hid

ABS_MOUSE_DESCRIPTOR = bytes((
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x02,        # Usage (Mouse)
    0xA1, 0x01,        # Collection (Application)
    0x85, 0x02,        #   Report ID (2)            <-- THE FIX
    0x09, 0x01,        #   Usage (Pointer)
    0xA1, 0x00,        #   Collection (Physical)
    # Buttons: 3 bits + 5 padding
    0x05, 0x09,        #     Usage Page (Button)
    0x19, 0x01,        #     Usage Minimum (1)
    0x29, 0x03,        #     Usage Maximum (3)
    0x15, 0x00,        #     Logical Minimum (0)
    0x25, 0x01,        #     Logical Maximum (1)
    0x95, 0x03,        #     Report Count (3)
    0x75, 0x01,        #     Report Size (1)
    0x81, 0x02,        #     Input (Data,Var,Abs)
    0x95, 0x01,        #     Report Count (1)
    0x75, 0x05,        #     Report Size (5)
    0x81, 0x03,        #     Input (Const) padding
    # Absolute X, Y: 16-bit, 0..32767
    0x05, 0x01,        #     Usage Page (Generic Desktop)
    0x09, 0x30,        #     Usage (X)
    0x09, 0x31,        #     Usage (Y)
    0x16, 0x00, 0x00,  #     Logical Minimum (0)
    0x26, 0xFF, 0x7F,  #     Logical Maximum (32767)
    0x75, 0x10,        #     Report Size (16)
    0x95, 0x02,        #     Report Count (2)
    0x81, 0x02,        #     Input (Data,Var,Abs)
    0xC0,              #   End Collection
    0xC0,              # End Collection
))

abs_mouse = usb_hid.Device(
    report_descriptor=ABS_MOUSE_DESCRIPTOR,
    usage_page=0x01,
    usage=0x02,
    report_ids=(2,),           # Report ID 2 (stock keyboard uses 1)
    in_report_lengths=(5,),    # buttons(1) + X(2) + Y(2); ID byte prepended by CP
    out_report_lengths=(0,),
)

usb_hid.enable((abs_mouse, usb_hid.Device.KEYBOARD))
