"""
code.py — Pico W HID injector (absolute mouse + keyboard) over WiFi.  v4

Matches boot.py v4: the absolute mouse is a Generic-Desktop Mouse (usage
0x01/0x02) carrying **Report ID 2**, enabled alongside the stock keyboard
(Report ID 1). The report ID is what makes the two collections coexist on one
HID interface; without it Windows rejects the whole interface (Code 10) and
NOTHING works. CircuitPython prepends the report-ID byte automatically, so
send_report(_report) is unchanged and _report stays 5 bytes.

Changes vs v2:
  - SELFTEST wrapped in try/except (a slow-to-enumerate host raises OSError
    "USB busy" before the HID interface is configured; that must not crash
    code.py before WiFi comes up).
  - WiFi resilience: toggle the radio off/on before connecting (clears the
    CircuitPython CYW43 "Unknown failure 1" state left by a soft reload), and
    hard-reset after MAX_FAILS consecutive failures for a clean boot.

Protocol (newline-terminated): M x,y / C / R / D / U / K name / T text /
  X k1+k2 / S ticks / H
EDIT: WIFI_SSID, WIFI_PASSWORD, SCREEN_W, SCREEN_H.
"""

import time
import wifi
import socketpool
import microcontroller
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

# ---------------- EDIT THESE ----------------
WIFI_SSID     = "WifiName"
WIFI_PASSWORD = "LAla3903.!"
# SCREEN_W/H must match the coordinate space the host sends — i.e. the HDMI
# capture frame the agent grounds on (1920x1080), NOT the target's native
# resolution. Absolute HID maps fraction-of-SCREEN_W onto the full display, so
# matching the capture is what makes clicks land. (Mismatch => systematic offset.)
SCREEN_W = 1920
SCREEN_H = 1080
PORT = 8000
CONN_TIMEOUT = 45        # seconds. FINITE per-connection recv timeout (never None): bounds how
                         # long a stuck/half-open peer can hold the serve loop before it recycles
                         # back to accept(). Must exceed the longest real gap between commands in
                         # a rollout (planning + a cold-model verify pass ~ up to ~30s).
SELFTEST = True          # fire a move on boot to prove HID without WiFi
# --------------------------------------------

print("=" * 40)
print("Pico HID injector booting (v4)")

# --- locate our custom absolute mouse (boot.py v4: usage 0x01/0x02) ---
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

_report = bytearray(5)        # buttons, xL, xH, yL, yH   (Report ID prepended by CP)
_cur_x = 0
_cur_y = 0

def _send():
    abs_mouse.send_report(_report)     # single report id (2) -> auto-used

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
        _report[0] &= (~bit) & 0xFF
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
    # function keys F1-F12: multi-char tokens NOT in _NAMED were silently dropped
    # before (so "alt+f4" sent only Alt -> Alt+F4 was a no-op; any F-key task failed).
    # adafruit_hid exposes Keycode.F1..F12, so resolve them explicitly.
    if len(t) >= 2 and t[0] == "f" and t[1:].isdigit() and 1 <= int(t[1:]) <= 12:
        return getattr(Keycode, t.upper(), None)
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
# try/except: on a slow-to-enumerate host send_report() raises OSError ("USB
# busy") before the HID interface is configured; skip the self-test then but
# still run it normally when USB is ready.
if SELFTEST and abs_mouse:
    try:
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
    except Exception as e:
        print("SELFTEST skipped (USB not ready yet):", e)

# ---------------- WiFi ----------------
MAX_FAILS = 5

def connect_wifi():
    print("connecting wifi to", WIFI_SSID, "...")
    try:
        # clear stale CYW43 radio state from a soft reload before connecting
        wifi.radio.enabled = False
        time.sleep(0.5)
        wifi.radio.enabled = True
        wifi.radio.connect(WIFI_SSID, WIFI_PASSWORD)
        print("WiFi OK, ip:", wifi.radio.ipv4_address)
        # Disable CYW43 WiFi power-save (modem-sleep). With it ON (the default),
        # the radio sleeps between beacons -> ~40-214ms jittery ping (a sawtooth)
        # and occasional beacon-miss disassociations that forcibly reset the
        # command TCP connection mid-run (the WinError 10054 that no-op'd every
        # click on 2026-06-19). PM_DISABLED keeps the radio awake: low, flat
        # latency (~2-5ms) at a small extra power cost. Must be (re)set after each
        # connect, since toggling wifi.radio for the resilience reconnect above can
        # restore the powersave default.
        try:
            import cyw43
            cyw43.set_power_management(cyw43.PM_DISABLED)
            print("WiFi power-save DISABLED (cyw43.PM_DISABLED)")
        except Exception as e:
            print("could not disable WiFi power-save:", e)
        return True
    except Exception as e:
        print("!!! WiFi FAILED:", e)
        return False

_fails = 0
while not connect_wifi():
    _fails += 1
    if _fails >= MAX_FAILS:
        print("WiFi failed %d times — hard reset for a clean boot" % _fails)
        time.sleep(1)
        microcontroller.reset()
    print("retrying wifi in 3s... (%d/%d)" % (_fails, MAX_FAILS))
    time.sleep(3)

# ---------------- socket server (with WiFi self-heal) ----------------
pool = socketpool.SocketPool(wifi.radio)

def make_server():
    srv = pool.socket(pool.AF_INET, pool.SOCK_STREAM)
    srv.setsockopt(pool.SOL_SOCKET, pool.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(1)
    srv.settimeout(20)   # accept() returns periodically so we can health-check WiFi while idle
    print("listening on 0.0.0.0:%d  (ip %s)" % (PORT, wifi.radio.ipv4_address))
    return srv

def wifi_ok():
    try:
        return bool(wifi.radio.connected) and (wifi.radio.ipv4_address is not None)
    except Exception:
        return False

server = make_server()

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
    # WiFi self-heal: if the radio dropped while idle (the "Pico went to sleep" symptom),
    # reconnect and rebuild the listening socket instead of sitting dead until a power-cycle.
    if not wifi_ok():
        print("WiFi dropped — reconnecting…")
        if connect_wifi():
            try:
                server.close()
            except Exception:
                pass
            server = make_server()
        else:
            time.sleep(3)
            continue
    print("waiting for connection...")
    try:
        conn, addr = server.accept()
    except OSError:
        continue   # accept() timed out (no client) -> loop and re-check WiFi
    print("conn from", addr)
    # FINITE timeout — NOT settimeout(None). A blocking recv with no timeout WEDGES the serve
    # loop forever on a half-open peer (client process killed, Wi-Fi blip): the loop never
    # returns to accept(), so every NEW connection is accepted by lwIP but never serviced — the
    # host sees WinError 10054 on its first command and the only recovery was a physical
    # power-cycle. With a finite timeout the recv raises on a stuck peer, we recycle the socket
    # and are back at accept() within CONN_TIMEOUT; r4_client reconnects transparently, so a
    # legitimate long gap just costs a cheap reconnect on the next command.
    conn.settimeout(CONN_TIMEOUT)
    pending = ""
    try:
        while True:
            try:
                n = conn.recv_into(_buf)
            except OSError as e:
                print("  recv timed out / peer stuck -> recycling conn:", e)
                break
            if n == 0:
                break
            pending += str(_buf[:n], "utf-8")
            while "\n" in pending:
                one, pending = pending.split("\n", 1)
                handle(one)
    except Exception as e:
        print("conn err:", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass
        print("closed")
