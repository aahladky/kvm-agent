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
  POST /hid/clear                   -> all-keys-up (release every held key/button;
                                        added 2026-07-20 with ApplianceClient.clear_hid)
  POST /hid/set_screen?w=&h=        -> update the pixel->wire-range scale factor at
                                        runtime (2026-07-19); see set_screen's docstring
                                        for why this exists.
Every response is JSON: {"ok":bool, "ack":str, "ms":float, "cmd":str}.

COMMAND LOGGING: every daemon invocation creates
``<runs-dir>/hid_bridge_<timestamp>/commands.jsonl`` unless ``--log`` names an
explicit file. The default runs directory is ``/home/aaron/runs`` on the appliance.
Each request is appended as one JSON line -- request params, the
Pico's actual wire-level response (code/online-bits/round-trip ms, decoded via
pikvm_proto.decode_code -- the closest thing to a genuine "did the target
receive this" signal without installing anything on the target), and total
HTTP-level timing. Previously zero: log_message() was a deliberate no-op and
nothing else persisted anything, so two overnight appliance crashes left no
forensic trail. See _cmd_* functions' return shape (ack, wire_info) below --
wire_info is None only for commands with no corresponding Pico roundtrip
(currently none; set_screen doesn't touch the Pico at all, handled separately).

Run:
  python3 hid_bridge.py                 # 0.0.0.0:8080, /dev/ttyAMA0, 1920x1080
  python3 hid_bridge.py --port 8080 --serial /dev/ttyAMA0 --screen-w 1920 --screen-h 1080
"""
import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from pikvm_proto import PicoHidLink, ProtoError, decode_code


LINK = None       # set in main()
SCREEN_W = 1920
SCREEN_H = 1080
CMD_LOG = None    # CommandLogger, set in main()


def _wire_info(link_result):
    """link_result is whatever a PicoHidLink method returned: a _roundtrip() dict
    ({"code","raw","ms","retries"}) for every real command now (2026-07-19 -- see
    pikvm_proto.py's higher-level methods, which used to return None and discard this).
    `retries` (2026-07-22, Phase 0 firmware hardening) surfaces host-side retry
    activity in every response and the wire log, not just on outright failure."""
    if link_result is None:
        return None
    info = decode_code(link_result["code"], link_result.get("raw"))
    info["wire_ms"] = link_result["ms"]
    info["retries"] = link_result.get("retries", 0)
    return info


def _cmd_move(q):
    x, y = int(q["x"][0]), int(q["y"][0])
    r = LINK.mouse_abs(x, y, SCREEN_W, SCREEN_H)
    return f"M {x},{y}", _wire_info(r)


def _cmd_click(q):
    r = LINK.click()
    return "C", _wire_info(r)


def _cmd_rclick(q):
    r = LINK.rclick()
    return "R", _wire_info(r)


def _cmd_down(q):
    r = LINK.button_down()
    return "D", _wire_info(r)


def _cmd_up(q):
    r = LINK.button_up()
    return "U", _wire_info(r)


def _cmd_home(q):
    r = LINK.mouse_abs(0, 0, SCREEN_W, SCREEN_H)
    return "H", _wire_info(r)


def _cmd_key(q):
    r = LINK.key_by_name(q["name"][0])
    return "K " + q["name"][0], _wire_info(r)


def _cmd_type(q):
    r = LINK.type_text(q["text"][0])
    return "T " + q["text"][0], _wire_info(r)


def _cmd_combo(q):
    r = LINK.combo(q["spec"][0])
    return "X " + q["spec"][0], _wire_info(r)


def _cmd_scroll(q):
    ticks = int(q["ticks"][0])
    r = LINK.mouse_wheel(ticks)
    return f"S {ticks}", _wire_info(r)


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
    return f"SET_SCREEN {SCREEN_W}x{SCREEN_H}", None   # no Pico roundtrip involved


def _cmd_probe(q):
    p = LINK.probe()
    # kbd/mouse flags = the firmware's view of whether each HID collection is online at
    # the target OS. The composite device can come up HALF-dead (keyboard alive, mouse
    # dead -- seen live 2026-07-18), so both must be surfaced for reset verification.
    # watchdog_rebooted/usb_suspended (2026-07-22, Phase 0 firmware hardening): a
    # hang or a suspended-bus event must be visible in /health, not just buried in
    # the wire log -- "make failure loud" applies to the appliance's own state too.
    ack = (f"PROBE caps={p['caps']} num={p['num']} scroll={p['scroll']} "
           f"kbd={int(p['kbd_online'])} mouse={int(p['mouse_online'])} "
           f"watchdog_rebooted={int(p['watchdog_rebooted'])} "
           f"usb_suspended={int(p.get('usb_suspended', 0))}")
    return ack, p


def _cmd_clear(q):
    r = LINK.clear_hid()
    return "CLR", _wire_info(r)


ROUTES = {
    "/hid/move": _cmd_move, "/hid/click": _cmd_click, "/hid/rclick": _cmd_rclick,
    "/hid/down": _cmd_down, "/hid/up": _cmd_up, "/hid/home": _cmd_home,
    "/hid/key": _cmd_key, "/hid/type": _cmd_type, "/hid/combo": _cmd_combo,
    "/hid/scroll": _cmd_scroll, "/hid/probe": _cmd_probe, "/hid/clear": _cmd_clear,
    "/hid/set_screen": _cmd_set_screen,
}


class CommandLogger:
    """Append-only JSONL log of every /hid/* request (2026-07-19) -- the fix for a
    confirmed-live gap: this appliance had ZERO persistent command logging (log_message()
    was a deliberate no-op, "the caller sees ACKs in the JSON" -- true, but only for that
    one synchronous caller, and nothing survived a crash). One line per request: request
    params, the Pico's actual wire-level response (decoded via pikvm_proto.decode_code --
    caps/num/scroll LEDs, kbd/mouse online bits, wire round-trip ms), and total HTTP-level
    timing. flush() after every write (not buffered) so a crash mid-run doesn't lose the
    tail -- exactly the forensic trail the two overnight 502s this project hit had none of.
    Thread-safe: ThreadingHTTPServer runs each request in its own thread."""

    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.lock = threading.Lock()

    def write(self, record):
        record["ts"] = time.time()
        line = json.dumps(record)
        with self.lock:
            with open(self.path, "a") as f:
                f.write(line + "\n")
                f.flush()


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
            ack, wire = fn(q)
            ms = round((time.time() - t0) * 1000.0, 1)
            return {"ok": True, "ack": ack, "ms": ms, "cmd": fn.__name__, "wire": wire}
        except ProtoError as e:
            ms = round((time.time() - t0) * 1000.0, 1)
            return {"ok": False, "ack": str(e), "ms": ms, "cmd": fn.__name__, "wire": None}

    def _log(self, path, q, result, http_ms):
        if CMD_LOG is None:
            return
        CMD_LOG.write({"path": path, "params": {k: v[0] for k, v in q.items()},
                       "ok": result.get("ok"), "ack": result.get("ack"),
                       "cmd": result.get("cmd"), "wire": result.get("wire"),
                       "wire_ms": result.get("ms"), "http_ms": http_ms})

    def _handle(self):
        t0 = time.time()
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/health":
            result = self._run(_cmd_probe, {})
            self._log(u.path, q, result, round((time.time() - t0) * 1000.0, 1))
            return self._json(200, {"ok": result["ok"], "port": LINK.port,
                                    "pico_acking": result["ok"], "probe": result["ack"],
                                    "screen_w": SCREEN_W, "screen_h": SCREEN_H})
        fn = ROUTES.get(u.path)
        if not fn:
            self._log(u.path, q, {"ok": False, "ack": "no such route", "cmd": None, "wire": None},
                       round((time.time() - t0) * 1000.0, 1))
            return self._json(404, {"ok": False, "error": "no such route", "path": u.path})
        try:
            result = self._run(fn, q)
        except (KeyError, ValueError, IndexError) as e:
            self._log(u.path, q, {"ok": False, "ack": f"bad params: {e}", "cmd": fn.__name__, "wire": None},
                       round((time.time() - t0) * 1000.0, 1))
            return self._json(400, {"ok": False, "error": f"bad params: {e}", "path": u.path})
        self._log(u.path, q, result, round((time.time() - t0) * 1000.0, 1))
        return self._json(200 if result["ok"] else 502, result)

    do_GET = _handle
    do_POST = _handle

    def log_message(self, *a):
        pass  # quiet on the console; CommandLogger is the real, persistent log now


def main():
    global LINK, SCREEN_W, SCREEN_H
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--serial", default="/dev/ttyAMA0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--timeout", type=float, default=1.0)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    # FALLBACK ONLY (2026-07-19) -- overwritten at runtime by /hid/set_screen, which
    # PicoEnv.__init__ (kvm_agent/hardware/env.py) pushes at every bring-up since
    # 2026-07-21. Not a source of truth: the launch-time default here has no way to
    # know what the target is actually rendering at.
    ap.add_argument("--screen-w", type=int, default=1920)
    ap.add_argument("--screen-h", type=int, default=1080)
    ap.add_argument("--runs-dir", default="/home/aaron/runs",
                    help="artifact root used when --log is omitted")
    ap.add_argument("--log",
                    help="explicit JSONL command log path; default is a fresh "
                         "<runs-dir>/hid_bridge_<timestamp>/commands.jsonl")
    args = ap.parse_args()

    global CMD_LOG
    log_path = args.log
    if not log_path:
        ts = time.strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(args.runs_dir, f"hid_bridge_{ts}", "commands.jsonl")
    CMD_LOG = CommandLogger(log_path)
    SCREEN_W, SCREEN_H = args.screen_w, args.screen_h
    LINK = PicoHidLink(args.serial, args.baud, args.timeout)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"hid_bridge (pikvm protocol) on http://{args.host}:{args.port}  "
          f"serial={args.serial}@{args.baud}  screen={SCREEN_W}x{SCREEN_H}  log={log_path}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
