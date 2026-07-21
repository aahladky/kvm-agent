"""
pikvm_proto.py -- wire protocol client for the ported PiKVM Pico HID firmware
(appliance/pico_fw/, RP2350/Pico 2 W port of github.com/pikvm/kvmd hid/pico).

Replaces the old ASCII "<seq> CMD arg\\n" protocol (appliance/pico/stage2_hid.py, now
_archive/firmware_old/appliance_pico/stage2_hid.py, CircuitPython, retired 2026-07-18) with
PiKVM's real binary framing: fixed 8-byte
frames, CRC16/MODBUS-checked, over the same UART wiring (Pi5 GPIO14/15 <-> Pico
GP0/GP1, uart0 @ 115200 -- see appliance/README.md).

Frame (host -> pico), 8 bytes:
  [0]     0x33 (MAGIC)
  [1]     command byte (see CMD_*)
  [2:6]   4 bytes of command-specific payload (zero-padded)
  [6:8]   CRC16/MODBUS of bytes[0:6], big-endian

Frame (pico -> host), 8 bytes:
  [0]     0x34 (MAGIC_RESP)
  [1]     high bit set (0x80) = PONG_OK, with caps/num/scroll LED + kbd/mouse
          online bits also packed in on every successful response; else an
          error code (CRC_ERROR 0x40 / INVALID_ERROR 0x45 / TIMEOUT_ERROR 0x48)
  [2:6]   output-mode/avail bits on success, 0 on error
  [6:8]   CRC16/MODBUS of bytes[0:6]

This module owns the ONE Pico-specific quirk worth documenting: the absolute-mouse
wire value is NOT the same range as the USB HID report. The firmware does
`x_usb = (x_proto + 32768) / 2` (ph_usb.c _mouse_abs_send_report) -- i.e. the wire
argument is a full signed s16 (-32768..32767) that maps LINEARLY to the USB report's
0..32767 range, not a direct 0..32767 pass-through. See _px_to_proto().
"""
import struct
import threading
import time

import serial


# ---- USB HID keyboard usage IDs (USB HID Usage Page 0x07) ----------------------
# Standard values -- these are the same numeric codes adafruit_hid.keycode.Keycode
# used (the retired CircuitPython firmware's table), so /hid/key and /hid/type's
# external contract (key NAMES, not codes) is unchanged.
_LETTERS = {chr(ord("a") + i): 0x04 + i for i in range(26)}          # a..z -> 0x04..0x1D
_DIGITS_ROW = {str((i + 1) % 10): 0x1E + i for i in range(10)}       # 1..9,0 -> 0x1E..0x27

KEYCODES = {
    **_LETTERS,
    **_DIGITS_ROW,
    "enter": 0x28, "return": 0x28,
    "esc": 0x29, "escape": 0x29,
    "backspace": 0x2A,
    "tab": 0x2B,
    "space": 0x2C,
    "minus": 0x2D, "equal": 0x2E,
    "leftbracket": 0x2F, "rightbracket": 0x30,
    "backslash": 0x31,
    "semicolon": 0x33, "quote": 0x34, "grave": 0x35,
    "comma": 0x36, "period": 0x37, "slash": 0x38,
    "capslock": 0x39, "caps_lock": 0x39,
    "f1": 0x3A, "f2": 0x3B, "f3": 0x3C, "f4": 0x3D, "f5": 0x3E, "f6": 0x3F,
    "f7": 0x40, "f8": 0x41, "f9": 0x42, "f10": 0x43, "f11": 0x44, "f12": 0x45,
    "printscreen": 0x46, "scrolllock": 0x47, "pause": 0x48,
    "insert": 0x49, "home": 0x4A, "pageup": 0x4B,
    "delete": 0x4C, "end": 0x4D, "pagedown": 0x4E,
    "right": 0x4F, "left": 0x50, "down": 0x51, "up": 0x52,
    "numlock": 0x53, "num_lock": 0x53,
    "ctrl": 0xE0, "control": 0xE0, "leftcontrol": 0xE0,
    "shift": 0xE1, "leftshift": 0xE1,
    "alt": 0xE2, "leftalt": 0xE2,
    "gui": 0xE3, "cmd": 0xE3, "win": 0xE3, "leftgui": 0xE3,
    "rightcontrol": 0xE4, "rightshift": 0xE5, "rightalt": 0xE6, "rightgui": 0xE7,
}
MOD_MIN, MOD_MAX = 0xE0, 0xE7  # matches firmware's ph_usb_kbd_send_key modifier range

# char -> (keycode_name, needs_shift), covers the printable-ASCII repertoire the
# old KeyboardLayoutUS(kbd).write() handled.
_SHIFT_SYMBOLS = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
    "(": "9", ")": "0", "_": "minus", "+": "equal", "{": "leftbracket", "}": "rightbracket",
    "|": "backslash", ":": "semicolon", '"': "quote", "~": "grave", "<": "comma",
    ">": "period", "?": "slash",
}
_PLAIN_SYMBOLS = {
    "-": "minus", "=": "equal", "[": "leftbracket", "]": "rightbracket", "\\": "backslash",
    ";": "semicolon", "'": "quote", "`": "grave", ",": "comma", ".": "period", "/": "slash",
    " ": "space",
}


def key_for_char(ch):
    """Return (keycode_name, needs_shift) for one printable ASCII character."""
    if ch in _SHIFT_SYMBOLS:
        return _SHIFT_SYMBOLS[ch], True
    if ch in _PLAIN_SYMBOLS:
        return _PLAIN_SYMBOLS[ch], False
    if ch.isalpha():
        return ch.lower(), ch.isupper()
    if ch.isdigit():
        return ch, False
    return None, False


# ---- wire protocol --------------------------------------------------------------
MAGIC, MAGIC_RESP = 0x33, 0x34
PONG_OK = 0x80
PONG_CAPS, PONG_SCROLL, PONG_NUM = 0x01, 0x02, 0x04
PONG_KBD_OFFLINE, PONG_MOUSE_OFFLINE = 0x08, 0x10

CMD_PING = 0x01
CMD_CLEAR_HID = 0x10
CMD_KBD_KEY = 0x11
CMD_MOUSE_ABS = 0x12
CMD_MOUSE_BUTTON = 0x13
CMD_MOUSE_WHEEL = 0x14

_BTN_LEFT_SEL, _BTN_LEFT_ST = 0x80, 0x08
_BTN_RIGHT_SEL, _BTN_RIGHT_ST = 0x40, 0x04
_BTN_MIDDLE_SEL, _BTN_MIDDLE_ST = 0x20, 0x02


def crc16(data):
    """CRC-16/MODBUS -- bit-for-bit port of the firmware's ph_crc16 (ph_tools.h)."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def _frame(cmd, payload=b""):
    payload = (payload + b"\x00" * 4)[:4]
    body = bytes([MAGIC, cmd]) + payload
    c = crc16(body)
    return body + bytes([(c >> 8) & 0xFF, c & 0xFF])


class ProtoError(RuntimeError):
    pass


class PicoHidLink:
    """One persistent UART link speaking PiKVM's binary frame protocol. Thread-safe;
    every public call is a blocking request/response round trip."""

    def __init__(self, port, baud=115200, timeout=1.0):
        self.ser = serial.Serial(port, baud, timeout=timeout)
        self.lock = threading.Lock()
        self.port = port
        time.sleep(0.2)
        self.ser.reset_input_buffer()

    def _roundtrip(self, cmd, payload=b""):
        req = _frame(cmd, payload)
        with self.lock:
            self.ser.reset_input_buffer()
            t0 = time.time()
            self.ser.write(req)
            resp = self.ser.read(8)
            ms = (time.time() - t0) * 1000.0
        if len(resp) != 8:
            raise ProtoError(f"short/no response ({len(resp)} bytes)")
        if resp[0] != MAGIC_RESP:
            raise ProtoError(f"bad magic 0x{resp[0]:02x}")
        if crc16(resp[:6]) != ((resp[6] << 8) | resp[7]):
            raise ProtoError("crc mismatch")
        code = resp[1]
        if not (code & PONG_OK):
            raise ProtoError(f"pico error code=0x{code:02x}")
        return {"code": code, "raw": resp, "ms": round(ms, 1)}

    # -- primitives --
    def ping(self):
        return self._roundtrip(CMD_PING)

    def clear_hid(self):
        return self._roundtrip(CMD_CLEAR_HID)

    def kbd_key(self, keycode, state):
        return self._roundtrip(CMD_KBD_KEY, bytes([keycode & 0xFF, 1 if state else 0]))

    def mouse_button(self, mask0, mask1=0):
        return self._roundtrip(CMD_MOUSE_BUTTON, bytes([mask0 & 0xFF, mask1 & 0xFF]))

    def mouse_abs(self, px, py, screen_w, screen_h):
        x_proto = _px_to_proto(px, screen_w)
        y_proto = _px_to_proto(py, screen_h)
        payload = struct.pack(">hh", x_proto, y_proto)
        return self._roundtrip(CMD_MOUSE_ABS, payload)

    def mouse_wheel(self, ticks):
        v = max(-127, min(127, int(ticks)))
        return self._roundtrip(CMD_MOUSE_WHEEL, bytes([0, v & 0xFF]))

    # -- higher-level actions (mirror the retired stage2_hid.py repertoire) --
    def tap_key(self, keycode, hold_s=0.02):
        self.kbd_key(keycode, True)
        time.sleep(hold_s)
        self.kbd_key(keycode, False)

    def key_by_name(self, name):
        kc = KEYCODES.get(name.strip().lower())
        if kc is None:
            raise ProtoError(f"unknown_key:{name}")
        self.tap_key(kc)

    def combo(self, spec):
        names = [p.strip().lower() for p in spec.split("+") if p.strip()]
        codes = [KEYCODES[n] for n in names if n in KEYCODES]
        if not codes:
            raise ProtoError(f"unknown_combo:{spec}")
        for kc in codes:
            self.kbd_key(kc, True)
        time.sleep(0.03)
        for kc in reversed(codes):
            self.kbd_key(kc, False)

    def type_text(self, text, hold_s=0.02, gap_s=0.03):
        # Pace every keystroke (hold + inter-key gap): with zero pacing, Windows drops
        # HID reports during focus transitions -- observed live 2026-07-18 as
        # "holo battery test" arriving as "holo bay te" (battery task notepad_type).
        for ch in text:
            name, needs_shift = key_for_char(ch)
            if name is None:
                continue  # unsupported char, skip rather than fail the whole string
            kc = KEYCODES[name]
            if needs_shift:
                self.kbd_key(KEYCODES["shift"], True)
                time.sleep(hold_s)
            self.kbd_key(kc, True)
            time.sleep(hold_s)
            self.kbd_key(kc, False)
            if needs_shift:
                self.kbd_key(KEYCODES["shift"], False)
            time.sleep(gap_s)

    def click(self, screen_w=None, screen_h=None):
        self.mouse_button(_BTN_LEFT_SEL | _BTN_LEFT_ST)
        time.sleep(0.03)
        self.mouse_button(_BTN_LEFT_SEL)

    def rclick(self):
        self.mouse_button(_BTN_RIGHT_SEL | _BTN_RIGHT_ST)
        time.sleep(0.03)
        self.mouse_button(_BTN_RIGHT_SEL)

    def button_down(self):
        self.mouse_button(_BTN_LEFT_SEL | _BTN_LEFT_ST)

    def button_up(self):
        self.mouse_button(_BTN_LEFT_SEL)

    def probe(self):
        r = self.ping()
        code = r["code"]
        return {
            "caps": 1 if code & PONG_CAPS else 0,
            "num": 1 if code & PONG_NUM else 0,
            "scroll": 1 if code & PONG_SCROLL else 0,
            "kbd_online": not (code & PONG_KBD_OFFLINE),
            "mouse_online": not (code & PONG_MOUSE_OFFLINE),
        }


def _px_to_proto(px, screen_dim):
    """pixel coord -> the firmware's signed wire range. The firmware computes
    x_usb = (x_proto + 32768) / 2, so x_proto = x_usb*2 - 32768 where x_usb is the
    linear 0..32767 USB-report position for this pixel."""
    frac = max(0.0, min(1.0, px / screen_dim))
    x_usb = round(frac * 32767)
    x_proto = x_usb * 2 - 32768
    return max(-32768, min(32767, x_proto))
