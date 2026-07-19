#!/usr/bin/env python3
"""
hid_bridge.py -- Pi 5 appliance HID service, speaking PiKVM's binary Pico HID
protocol (pikvm_proto.py) over the wired UART link.

2026-07-18: rewritten from the ASCII "<seq> CMD arg\\n" protocol against a custom
CircuitPython firmware to PiKVM's own CRC16-framed binary protocol against a real
port of github.com/pikvm/kvmd's Pico HID firmware to RP2350/Pico 2 W (see
appliance/pico_fw/ and [[pikvm_hid_rp2350_port]] memory) -- the CircuitPython
firmware was structurally unsound (no ACK, composite-collection independent
death) and got replaced wholesale rather than re-patched.

The HTTP surface is UNCHANGED from the prior version, so kvm_agent/hardware/
appliance.py (ApplianceClient) needs no changes:

  GET  /health                      -> {ok, port, pico_acking, probe}
  POST /hid/move?x=&y=              -> absolute move (pixel coords; internally
                                        scaled to the firmware's wire range)
  POST /hid/click | /rclick | /down | /up | /home
  POST /hid/key?name=enter
  POST /hid/type?text=hello%20world
  POST /hid/combo?spec=ctrl%2Ba     (ctrl+a)
  POST /hid/scroll?ticks=3
  GET  /hid/probe                   -> keyboard liveness (LED readback)
  POST /hid/set_screen?w=&h=        -> update the pixel->wire-range scale factor at
                                        runtime (2026-07-19); see set_screen's docstring
                                        for why this exists.
Every response is JSON: {"ok":bool, "ack":str, "ms":float, "cmd":str}.

Run:
  python3 hid_bridge.py                 # 0.0.0.0:8080, /dev/ttyAMA0, 1920x1080
  python3 hid_bridge.py --port 8080 --serial /dev/ttyAMA0 --screen-w 1920 --screen-h 1080
"""
import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from pikvm_proto import PicoHidLink, ProtoError


LINK = None       # set in main()
SCREEN_W = 1920
SCREEN_H = 1080


def _cmd_move(q):
    x, y = int(q["x"][0]), int(q["y"][0])
    LINK.mouse_abs(x, y, SCREEN_W, SCREEN_H)
    return f"M {x},{y}"


def _cmd_click(q):
    LINK.click()
    return "C"


def _cmd_rclick(q):
    LINK.rclick()
    return "R"


def _cmd_down(q):
    LINK.button_down()
    return "D"


def _cmd_up(q):
    LINK.button_up()
    return "U"


def _cmd_home(q):
    LINK.mouse_abs(0, 0, SCREEN_W, SCREEN_H)
    return "H"


def _cmd_key(q):
    LINK.key_by_name(q["name"][0])
    return "K " + q["name"][0]


def _cmd_type(q):
    LINK.type_text(q["text"][0])
    return "T " + q["text"][0]


def _cmd_combo(q):
    LINK.combo(q["spec"][0])
    return "X " + q["spec"][0]


def _cmd_scroll(q):
    ticks = int(q["ticks"][0])
    LINK.mouse_wheel(ticks)
    return f"S {ticks}"


def _cmd_set_screen(q):
    """Update SCREEN_W/SCREEN_H at runtime instead of only at process launch (2026-07-19).

    Before this, the bridge's pixel->wire-range scale factor was a `--screen-w`/`--screen-h`
    CLI arg fixed for the process's lifetime, defaulting to 1920x1080 -- the SAME hardcoded
    default independently assumed on the host side (kvm_agent.config.CFG.screen_w/h). The
    two had to be kept in sync by convention, with nothing catching a drift if the physical
    capture card ever negotiated something other than 1920x1080 (cv2's `cap.set()` is a
    REQUEST, not a guarantee -- V4L2 can silently fall back to a supported mode). This
    endpoint lets the host tell the bridge what it ACTUALLY captured, once, right after
    opening the capture device -- see kvm_agent/hardware/env.py PicoEnv.__init__ for the
    caller. No Pico firmware involved: SCREEN_W/H is a plain Python global here, used only
    to compute the wire-range scale in mouse_abs() -- changing it takes effect on the very
    next /hid/move call, no restart needed.
    """
    global SCREEN_W, SCREEN_H
    SCREEN_W, SCREEN_H = int(q["w"][0]), int(q["h"][0])
    return f"SET_SCREEN {SCREEN_W}x{SCREEN_H}"


def _cmd_probe(q):
    p = LINK.probe()
    # kbd/mouse flags = the firmware's view of whether each HID collection is online at
    # the target OS. The composite device can come up HALF-dead (keyboard alive, mouse
    # dead -- seen live 2026-07-18), so both must be surfaced for reset verification.
    return (f"PROBE caps={p['caps']} num={p['num']} scroll={p['scroll']} "
            f"kbd={int(p['kbd_online'])} mouse={int(p['mouse_online'])}")


ROUTES = {
    "/hid/move": _cmd_move, "/hid/click": _cmd_click, "/hid/rclick": _cmd_rclick,
    "/hid/down": _cmd_down, "/hid/up": _cmd_up, "/hid/home": _cmd_home,
    "/hid/key": _cmd_key, "/hid/type": _cmd_type, "/hid/combo": _cmd_combo,
    "/hid/scroll": _cmd_scroll, "/hid/probe": _cmd_probe, "/hid/set_screen": _cmd_set_screen,
}


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _run(self, fn, q):
        t0 = time.time()
        try:
            ack = fn(q)
            ms = round((time.time() - t0) * 1000.0, 1)
            return {"ok": True, "ack": ack, "ms": ms, "cmd": fn.__name__}
        except ProtoError as e:
            ms = round((time.time() - t0) * 1000.0, 1)
            return {"ok": False, "ack": str(e), "ms": ms, "cmd": fn.__name__}

    def _handle(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/health":
            result = self._run(_cmd_probe, {})
            return self._json(200, {"ok": result["ok"], "port": LINK.port,
                                    "pico_acking": result["ok"], "probe": result["ack"],
                                    "screen_w": SCREEN_W, "screen_h": SCREEN_H})
        fn = ROUTES.get(u.path)
        if not fn:
            return self._json(404, {"ok": False, "error": "no such route", "path": u.path})
        try:
            result = self._run(fn, q)
        except (KeyError, ValueError, IndexError) as e:
            return self._json(400, {"ok": False, "error": f"bad params: {e}", "path": u.path})
        return self._json(200 if result["ok"] else 502, result)

    do_GET = _handle
    do_POST = _handle

    def log_message(self, *a):
        pass  # quiet; the caller sees ACKs in the JSON


def main():
    global LINK, SCREEN_W, SCREEN_H
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--serial", default="/dev/ttyAMA0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--timeout", type=float, default=1.0)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--screen-w", type=int, default=1920)
    ap.add_argument("--screen-h", type=int, default=1080)
    args = ap.parse_args()

    SCREEN_W, SCREEN_H = args.screen_w, args.screen_h
    LINK = PicoHidLink(args.serial, args.baud, args.timeout)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"hid_bridge (pikvm protocol) on http://{args.host}:{args.port}  "
          f"serial={args.serial}@{args.baud}  screen={SCREEN_W}x{SCREEN_H}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
