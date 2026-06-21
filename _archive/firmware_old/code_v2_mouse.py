"""
code.py — Pico W HID injector (absolute mouse + keyboard) over WiFi.  v2

Changes from v1 (the debugging pass):
  - Report is now 5 bytes, NO report id (matches new boot.py). send_report(_report).
  - Fixed _set_btn byte-mask bug (~bit & 0xFF).
  - Bind socket to 0.0.0.0 (not the device IP) — robust across reconnects.
  - Wrapped WiFi/bind in try/except with prints so failures are VISIBLE, not silent.
  - BOOT SELF-TEST: fires a center move + a small wiggle on startup so you can
    confirm HID works WITHOUT sending anything over WiFi. Watch the cursor of
    whatever machine the Pico is plugged into when it boots.
  - Verbose: prints every command received and every report sent.

Protocol (newline-terminated): M x,y / C / R / D / U / K code / T text /
  X k1+k2 / S ticks / H
EDIT: WIFI_SSID, WIFI_PASSWORD, SCREEN_W, SCREEN_H.
"""

import time
import wifi
import socketpool
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

# ---------------- EDIT THESE ----------------
WIFI_SSID     = "YOUR_SSID"
WIFI_PASSWORD = "YOUR_PASS"
SCREEN_W = 1920
SCREEN_H = 1080
PORT = 8000
SELFTEST = True          # fire a move on boot to prove HID without WiFi
# --------------------------------------------

print("=" * 40)
print("Pico HID injector booting")

# --- locate our custom absolute mouse ---
abs_mouse = None
for d in usb_hid.devices:
    print("HID device: usage_page", d.usage_page, "usage", d.usage)
    if d.usage_page == 0x01 and d.usage == 0x02:
        abs_mouse = d
if abs_mouse is None:
    print("!!! ABS MOUSE NOT FOUND — boot.py didn't register it")
else:
    print("abs_mouse OK")

kbd = Keyboard(usb_hid.devices)

_report = bytearray(5)        # buttons, xL, xH, yL, yH   (NO report id)
_cur_x = 0
_cur_y = 0

def _send():
    abs_mouse.send_report(_report)     # no report id now

def _to_abs(px, py):
    ax = max(0, min(32767, int(px * 32767 / SCREEN_W)))
    ay = max(0, min(32767, int(py * 32767 / SCREEN_H)))
    return ax, ay

def move_to(px, py):
    global _cur_x, _cur_y
    _cur_x, _cur_y = _to_abs(px, py)
    _report[0] = _report[0] & 0x07
    _report[1] = _cur_x & 0xFF
    _report[2] = (_cur_x >> 8) & 0xFF
    _report[3] = _cur_y & 0xFF
    _report[4] = (_cur_y >> 8) & 0xFF
    _send()
    print("  move ->", px, py, "(abs", _cur_x, _cur_y, ")")

def _set_btn(bit, down):
    if down:
        _report[0] |= bit
    else:
        _report[0] &= (~bit) & 0xFF       # FIX: mask to a byte
    _send()

def click(bit=0x01):
    _set_btn(bit, True)
    time.sleep(0.03)
    _set_btn(bit, False)

# --- key handling ---
_NAMED = {
    "ctrl": Keycode.LEFT_CONTROL, "control": Keycode.LEFT_CONTROL,
    "shift": Keycode.LEFT_SHIFT, "alt": Keycode.LEFT_ALT,
    "gui": Keycode.LEFT_GUI, "cmd": Keycode.LEFT_GUI, "win": Keycode.LEFT_GUI,
    "enter": Keycode.ENTER, "return": Keycode.ENTER, "esc": Keycode.ESCAPE,
    "escape": Keycode.ESCAPE, "tab": Keycode.TAB, "space": Keycode.SPACE,
    "backspace": Keycode.BACKSPACE, "delete": Keycode.DELETE,
    "up": Keycode.UP_ARROW, "down": Keycode.DOWN_ARROW,
    "left": Keycode.LEFT_ARROW, "right": Keycode.RIGHT_ARROW,
    "home": Keycode.HOME, "end": Keycode.END,
    "pageup": Keycode.PAGE_UP, "pagedown": Keycode.PAGE_DOWN,
}

def _keycode_for(token):
    t = token.strip().lower()
    if t in _NAMED:
        return _NAMED[t]
    if len(t) == 1:
        try:
            return getattr(Keycode, t.upper())
        except AttributeError:
            pass
    return None

def type_text(s):
    from adafruit_hid.keyboard_layout_us import KeyboardLayoutUS
    KeyboardLayoutUS(kbd).write(s)

def combo(spec):
    codes = [c for c in (_keycode_for(p) for p in spec.split("+")) if c]
    if codes:
        kbd.press(*codes); time.sleep(0.03); kbd.release_all()

def tap(token):
    kc = _keycode_for(token)
    if kc:
        kbd.press(kc); time.sleep(0.02); kbd.release_all()

# --------- BOOT SELF-TEST (no WiFi needed) ---------
if SELFTEST and abs_mouse:
    print("SELFTEST: moving cursor center, then corners in 2s...")
    time.sleep(2)
    move_to(SCREEN_W // 2, SCREEN_H // 2)
    time.sleep(0.6)
    move_to(0, 0)
    time.sleep(0.6)
    move_to(SCREEN_W, SCREEN_H)
    time.sleep(0.6)
    move_to(SCREEN_W // 2, SCREEN_H // 2)
    print("SELFTEST done — did the cursor move?")

# ---------------- WiFi ----------------
def connect_wifi():
    print("connecting wifi to", WIFI_SSID, "...")
    try:
        wifi.radio.connect(WIFI_SSID, WIFI_PASSWORD)
        print("WiFi OK, ip:", wifi.radio.ipv4_address)
        return True
    except Exception as e:
        print("!!! WiFi FAILED:", e)
        return False

while not connect_wifi():
    print("retrying wifi in 3s...")
    time.sleep(3)

# ---------------- socket server ----------------
pool = socketpool.SocketPool(wifi.radio)
server = pool.socket(pool.AF_INET, pool.SOCK_STREAM)
server.setsockopt(pool.SOL_SOCKET, pool.SO_REUSEADDR, 1)
server.bind(("0.0.0.0", PORT))      # FIX: all interfaces, not the device IP
server.listen(1)
print("listening on 0.0.0.0:%d  (ip %s)" % (PORT, wifi.radio.ipv4_address))

_buf = bytearray(256)

def handle(line):
    line = line.strip()
    if not line:
        return
    cmd = line[0]
    arg = line[1:].strip()
    print("CMD:", repr(line))
    try:
        if cmd == "M":
            xs, ys = arg.split(",")
            move_to(int(xs), int(ys))
        elif cmd == "C": click(0x01)
        elif cmd == "R": click(0x02)
        elif cmd == "D": _set_btn(0x01, True)
        elif cmd == "U": _set_btn(0x01, False)
        elif cmd == "H": move_to(0, 0)
        elif cmd == "K": tap(arg)
        elif cmd == "T": type_text(arg)
        elif cmd == "X": combo(arg)
        elif cmd == "S": move_to(_cur_x, _cur_y)  # scroll removed for now
        else: print("unknown:", line)
    except Exception as e:
        print("  handle err:", e)

while True:
    print("waiting for connection...")
    conn, addr = server.accept()
    print("conn from", addr)
    pending = ""
    try:
        while True:
            n = conn.recv_into(_buf)
            if n == 0:
                break
            pending += str(_buf[:n], "utf-8")
            while "\n" in pending:
                one, pending = pending.split("\n", 1)
                handle(one)
    except Exception as e:
        print("conn err:", e)
    finally:
        conn.close()
        print("closed")
