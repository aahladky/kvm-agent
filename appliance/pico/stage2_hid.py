"""
stage2_hid.py -- Pico 2 W, Stage-2 firmware for the Pi5+Pico appliance.

Stage 1 proved the wired UART link + seq/ACK. Stage 2 adds the real USB-HID
command set behind that same protocol: every command executes the HID action
and THEN replies with an ACK, so the controller learns success/failure instead
of firing blind. No WiFi. Deploy as code.py on CIRCUITPY (keep boot.py -- it
defines the composite HID descriptor: keyboard Report ID 1 + absolute mouse
Report ID 2, 6-byte report [buttons,xL,xH,yL,yH,wheel]).

HID action code (move/click/scroll/type/combo/tap, absolute 0..32767 mapping,
caps-lock self-correct) is lifted verbatim from the proven WiFi firmware
(code_wifi_v4) -- only the transport changed (WiFi -> UART) and each command is
now wrapped in the seq/ACK envelope.

Protocol (newline-framed ASCII), controller -> pico:  "<seq> <CMD> <args>\n"
  M x,y   absolute move          C  left click       R  right click
  D       left button down       U  left button up   H  home (0,0)
  K name  tap a named key        T text  type text   X  k1+k2 combo
  S ticks scroll wheel (+up/-down)
  PROBE   keyboard liveness (reads host-reported lock LEDs)
pico -> controller:
  "<seq> OK\n"                    success (ACK is sent AFTER the action completes)
  "<seq> OK caps=<0|1> num=<0|1> scroll=<0|1>\n"   (PROBE)
  "<seq> ERR <reason>\n"         parse/exec failure

Note on liveness: the keyboard collection is verifiable end-to-end via the host
LED readback (PROBE). The mouse collection has NO such back-channel in USB HID --
its per-action ground truth is the screen, not this ACK. The ACK only proves
"HID report sent," never "the OS moved the cursor."
"""
import time
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

SCREEN_W = 1920
SCREEN_H = 1080
BAUD = 115200

import board
import busio
uart = busio.UART(board.GP0, board.GP1, baudrate=BAUD, timeout=0.01,
                  receiver_buffer_size=512)

# --- locate the custom absolute mouse (boot.py: usage_page 0x01, usage 0x02) ---
abs_mouse = None
for d in usb_hid.devices:
    if d.usage_page == 0x01 and d.usage == 0x02:
        abs_mouse = d
kbd = Keyboard(usb_hid.devices)

_report = bytearray(6)        # buttons, xL, xH, yL, yH, wheel  (Report ID 2 prepended by CP)
_cur_x = 0
_cur_y = 0


def _sendrep():
    abs_mouse.send_report(_report)


def _to_abs(px, py):
    ax = max(0, min(32767, int(px * 32767 / SCREEN_W)))
    ay = max(0, min(32767, int(py * 32767 / SCREEN_H)))
    return ax, ay


def move_to(px, py):
    global _cur_x, _cur_y
    _cur_x, _cur_y = _to_abs(px, py)
    _report[0] = _report[0] & 0x07          # keep button bits, move
    _report[1] = _cur_x & 0xFF
    _report[2] = (_cur_x >> 8) & 0xFF
    _report[3] = _cur_y & 0xFF
    _report[4] = (_cur_y >> 8) & 0xFF
    _sendrep()


def _set_btn(bit, down):
    if down:
        _report[0] |= bit
    else:
        _report[0] &= (~bit) & 0xFF
    _sendrep()


def click(bit=0x01):
    _set_btn(bit, True)
    time.sleep(0.03)
    _set_btn(bit, False)


def scroll(ticks):
    ticks = max(-127, min(127, int(ticks)))
    step = 1 if ticks >= 0 else -1
    for _ in range(abs(ticks)):
        _report[5] = step & 0xFF
        _sendrep()
        time.sleep(0.01)
    _report[5] = 0
    _sendrep()


_NAMED = {
    "ctrl": Keycode.LEFT_CONTROL, "control": Keycode.LEFT_CONTROL,
    "shift": Keycode.LEFT_SHIFT, "alt": Keycode.LEFT_ALT,
    "gui": Keycode.LEFT_GUI, "cmd": Keycode.LEFT_GUI, "win": Keycode.LEFT_GUI,
    "enter": Keycode.ENTER, "return": Keycode.ENTER, "esc": Keycode.ESCAPE,
    "escape": Keycode.ESCAPE, "tab": Keycode.TAB, "space": Keycode.SPACE,
    "backspace": Keycode.BACKSPACE, "delete": Keycode.DELETE,
    "capslock": Keycode.CAPS_LOCK, "caps_lock": Keycode.CAPS_LOCK,
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
    if len(t) >= 2 and t[0] == "f" and t[1:].isdigit() and 1 <= int(t[1:]) <= 12:
        return getattr(Keycode, t.upper(), None)
    return None


def _clear_caps_lock():
    try:
        if kbd.led_on(Keyboard.LED_CAPS_LOCK):
            kbd.press(Keycode.CAPS_LOCK); time.sleep(0.02); kbd.release_all()
            time.sleep(0.05)
    except Exception:
        pass


def type_text(s):
    from adafruit_hid.keyboard_layout_us import KeyboardLayoutUS
    _clear_caps_lock()
    KeyboardLayoutUS(kbd).write(s)


def combo(spec):
    codes = [c for c in (_keycode_for(p) for p in spec.split("+")) if c]
    if codes:
        kbd.press(*codes); time.sleep(0.03); kbd.release_all()


def tap(token):
    kc = _keycode_for(token)
    if kc is None:
        raise ValueError("unknown_key:" + token)
    kbd.press(kc); time.sleep(0.02); kbd.release_all()


def _probe():
    caps = 1 if kbd.led_on(Keyboard.LED_CAPS_LOCK) else 0
    num = 1 if kbd.led_on(Keyboard.LED_NUM_LOCK) else 0
    scr = 1 if kbd.led_on(Keyboard.LED_SCROLL_LOCK) else 0
    return "caps=%d num=%d scroll=%d" % (caps, num, scr)


def _dispatch(cmd, arg):
    """Run one command; return the OK payload string (may be '')."""
    if cmd in ("M", "C", "R", "D", "U", "H", "S") and abs_mouse is None:
        raise RuntimeError("no_abs_mouse")
    if cmd == "M":
        xs, ys = arg.split(",")
        move_to(int(xs), int(ys))
    elif cmd == "C":
        click(0x01)
    elif cmd == "R":
        click(0x02)
    elif cmd == "D":
        _set_btn(0x01, True)
    elif cmd == "U":
        _set_btn(0x01, False)
    elif cmd == "H":
        move_to(0, 0)
    elif cmd == "K":
        tap(arg)
    elif cmd == "T":
        type_text(arg)
    elif cmd == "X":
        combo(arg)
    elif cmd == "S":
        scroll(int(arg))
    elif cmd == "PROBE":
        return _probe()
    else:
        raise ValueError("unknown_cmd")
    return ""


print("stage2_hid up: UART0 GP0(TX)/GP1(RX) @", BAUD,
      "| abs_mouse", "OK" if abs_mouse else "MISSING")

_buf = b""


def _reply(s):
    uart.write((s + "\n").encode())


while True:
    n = uart.in_waiting
    if not n:
        time.sleep(0.001)
        continue
    chunk = uart.read(n)
    if not chunk:
        continue
    _buf += chunk
    while b"\n" in _buf:
        line, _buf = _buf.split(b"\n", 1)
        try:
            text = line.decode().strip()
        except Exception:
            _reply("0 ERR decode")
            continue
        if not text:
            continue
        parts = text.split(" ", 2)
        seq = parts[0]
        cmd = parts[1].upper() if len(parts) > 1 else ""
        arg = parts[2] if len(parts) > 2 else ""
        try:
            payload = _dispatch(cmd, arg)
            _reply(seq + " OK" + ((" " + payload) if payload else ""))
        except Exception as e:
            _reply(seq + " ERR " + str(e).replace("\n", " ")[:60])
