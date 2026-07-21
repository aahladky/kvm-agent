#!/usr/bin/env python3
"""
stage2_verify.py -- main-host verifier for Stage 2 of the Pi5+Pico appliance.

Runs on the MAIN HOST (where the Pico's USB HID is enumerated). For each HID
command it: (1) fires the command through the appliance path -- ssh to the Pi ->
send.py -> UART -> Pico -> USB HID -> this host -- and (2) reads the resulting
raw input events off the Pico's own input devices, which it GRABS exclusively
(EVIOCGRAB) so the events never reach the desktop. So we verify the full
UART->HID path against ground truth (the kernel input events) with zero desktop
disruption -- no cursor thrown around, no stray clicks/keys into real windows.

Requires: python-evdev on this host; the `input` group; SSH key auth to the Pi
with appliance/pi5/send.py staged at ~/send.py.

  python3 stage2_verify.py --pi aaron@192.168.0.29
"""
import argparse
import subprocess
import sys
import time

try:
    from evdev import InputDevice, ecodes, list_devices
except ImportError:
    sys.exit("python-evdev missing: pip install evdev")

MOUSE_ID = "usb-Raspberry_Pi_Pico_2_W_*-if03-event-mouse"
KBD_ID = "usb-Raspberry_Pi_Pico_2_W_*-if03-event-kbd"


def find_pico_devices():
    """Locate the Pico's mouse+keyboard event devices by name (robust to eventN renumbering)."""
    mouse = kbd = None
    for path in list_devices():
        try:
            d = InputDevice(path)
        except Exception:
            continue
        n = d.name or ""
        if "Pico 2 W" in n and "Mouse" in n:
            mouse = d
        elif "Pico 2 W" in n and "Keyboard" in n:
            kbd = d
    return mouse, kbd


class Grabbed:
    """Context manager: grab devices exclusively so the desktop doesn't see test input."""
    def __init__(self, devs):
        self.devs = [d for d in devs if d]
    def __enter__(self):
        for d in self.devs:
            d.grab()
        return self
    def __exit__(self, *a):
        for d in self.devs:
            try:
                d.ungrab()
            except Exception:
                pass


def drain(devs, dur=0.4):
    """Collect (type, code, value) events across devs for `dur` seconds after a command."""
    import select
    evs = []
    fds = {d.fd: d for d in devs if d}
    deadline = time.time() + dur
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        r, _, _ = select.select(list(fds), [], [], remaining)
        for fd in r:
            for e in fds[fd].read():
                evs.append((e.type, e.code, e.value))
    return evs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pi", default="aaron@192.168.0.29", help="user@host of the Pi 5")
    ap.add_argument("--key", default=None, help="ssh identity file")
    ap.add_argument("--send", default="~/send.py", help="path to send.py on the Pi")
    args = ap.parse_args()

    ssh = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
           "-o", "LogLevel=ERROR"]
    if args.key:
        ssh += ["-i", args.key]
    ssh += [args.pi]

    mouse, kbd = find_pico_devices()
    if not mouse or not kbd:
        sys.exit(f"could not find Pico input devices (mouse={mouse}, kbd={kbd}) -- is it plugged in?")
    print(f"mouse: {mouse.path} ({mouse.name})\nkbd:   {kbd.path} ({kbd.name})")

    def send(cmd):
        p = subprocess.run(ssh + [f"python3 {args.send} {cmd!r}"],
                           capture_output=True, text=True, timeout=15)
        return p.returncode == 0, (p.stdout + p.stderr).strip()

    results = []
    def check(name, cond, detail=""):
        results.append((name, cond))
        print(("ok  " if cond else "FAIL") + f"  {name}" + (f"  -- {detail}" if detail else ""))

    with Grabbed([mouse, kbd]):
        drain([mouse, kbd], 0.2)  # flush anything pending

        # 1. PROBE -- keyboard liveness ACK
        ok, ack = send("PROBE")
        check("PROBE acks OK with led state", ok and "caps=" in ack, ack)

        # 2-4. absolute moves -> ABS_X/ABS_Y at the expected 0..32767 mapping.
        # Order so each move is a real CHANGE: an absolute pointer emits ABS events
        # only when the value differs from the last, so moving to a coord you're
        # already at yields nothing (not a failure). center -> 0,0 -> max all differ.
        send("M 960,540")   # seed a known non-zero start, ignore result
        drain([mouse])
        for label, cmd, ex, ey in [
            ("move 0,0", "M 0,0", 0, 0),
            ("move 1920,1080 (max)", "M 1920,1080", 32767, 32767),
            ("move 960,540 (center)", "M 960,540", 16383, 16383),
        ]:
            ok, ack = send(cmd)
            evs = drain([mouse])
            xs = [v for (t, c, v) in evs if t == ecodes.EV_ABS and c == ecodes.ABS_X]
            ys = [v for (t, c, v) in evs if t == ecodes.EV_ABS and c == ecodes.ABS_Y]
            gx, gy = (xs[-1] if xs else None), (ys[-1] if ys else None)
            good = ok and gx is not None and gy is not None and abs(gx - ex) <= 40 and abs(gy - ey) <= 40
            check(label, good, f"ack={ack!r} got=({gx},{gy}) want~({ex},{ey})")

        # 5. left click -> BTN_LEFT down(1) then up(0)
        ok, ack = send("C")
        evs = drain([mouse])
        btn = [v for (t, c, v) in evs if t == ecodes.EV_KEY and c == ecodes.BTN_LEFT]
        check("left click -> BTN_LEFT 1,0", ok and (1 in btn) and (0 in btn), f"ack={ack!r} btn={btn}")

        # 6. type "hi" -> KEY_H and KEY_I press events
        ok, ack = send("T hi")
        evs = drain([kbd], 0.6)
        keys = [c for (t, c, v) in evs if t == ecodes.EV_KEY and v == 1]
        check("type 'hi' -> KEY_H,KEY_I", ok and ecodes.KEY_H in keys and ecodes.KEY_I in keys,
              f"ack={ack!r} keys={keys}")

        # 7. combo ctrl+a -> KEY_LEFTCTRL + KEY_A
        ok, ack = send("X ctrl+a")
        evs = drain([kbd])
        keys = [c for (t, c, v) in evs if t == ecodes.EV_KEY and v == 1]
        check("combo ctrl+a -> LEFTCTRL+A", ok and ecodes.KEY_LEFTCTRL in keys and ecodes.KEY_A in keys,
              f"ack={ack!r} keys={keys}")

        # 8. scroll up 3 -> REL_WHEEL events
        ok, ack = send("S 3")
        evs = drain([mouse])
        wheel = [v for (t, c, v) in evs if t == ecodes.EV_REL and c == ecodes.REL_WHEEL]
        check("scroll 3 -> REL_WHEEL", ok and len(wheel) >= 1, f"ack={ack!r} wheel={wheel}")

    npass = sum(1 for _, c in results if c)
    print(f"\n{npass}/{len(results)} checks passed")
    print("STAGE 2:", "PASS" if npass == len(results) else "FAIL")
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
