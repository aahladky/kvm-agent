#!/usr/bin/env python3
"""
hid_bridge.py -- Pi 5 appliance HID service (Stage 5, HID-only).

Exposes the Pico HID over an HTTP API so the main-host Holo loop drives input
with one network call and gets the REAL per-command ACK back (not the old
fire-and-forget ""). Wraps the Stage-2 UART protocol: holds ONE persistent
serial connection to the Pico, serializes commands under a lock, seq-numbers
each, and returns the Pico's ACK.

Capture is intentionally NOT here (Stage 4, deferred) -- frames still come off
the host capture card for now.

Endpoints (GET or POST; params via query string, text url-encoded):
  GET  /health                      -> {ok, port, pico_acking, probe}
  POST /hid/move?x=&y=              -> absolute move
  POST /hid/click | /rclick | /down | /up | /home
  POST /hid/key?name=enter
  POST /hid/type?text=hello%20world
  POST /hid/combo?spec=ctrl%2Ba     (ctrl+a)
  POST /hid/scroll?ticks=3
  GET  /hid/probe                   -> keyboard liveness (LED readback)
Every response is JSON: {"ok":bool, "ack":str, "ms":float, "cmd":str}.

Run:
  python3 hid_bridge.py                 # 0.0.0.0:8080, /dev/ttyAMA0
  python3 hid_bridge.py --port 8080 --serial /dev/ttyAMA0
"""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import serial


class UartBridge:
    """One persistent serial link to the Pico; thread-safe seq/ACK command send."""

    def __init__(self, port, baud, timeout):
        self.ser = serial.Serial(port, baud, timeout=timeout)
        self.lock = threading.Lock()
        self.seq = int(time.time() * 1000) % 100000
        self.port = port
        time.sleep(0.2)
        self.ser.reset_input_buffer()

    def send(self, cmd):
        with self.lock:
            self.seq = (self.seq + 1) % 1000000
            seq = self.seq
            self.ser.reset_input_buffer()
            t0 = time.time()
            self.ser.write(f"{seq} {cmd}\n".encode())
            line = self.ser.readline()
            ms = (time.time() - t0) * 1000.0
        resp = line.decode(errors="replace").strip() if line else ""
        toks = resp.split()
        ok = len(toks) >= 2 and toks[0] == str(seq) and toks[1] == "OK"
        return {"ok": ok, "ack": resp, "ms": round(ms, 1), "cmd": cmd}


BRIDGE = None  # set in main()

# path -> function(query_dict) -> pico command string
def _cmd_move(q):   return f"M {int(q['x'][0])},{int(q['y'][0])}"
def _cmd_click(q):  return "C"
def _cmd_rclick(q): return "R"
def _cmd_down(q):   return "D"
def _cmd_up(q):     return "U"
def _cmd_home(q):   return "H"
def _cmd_key(q):    return "K " + q["name"][0]
def _cmd_type(q):   return "T " + q["text"][0]
def _cmd_combo(q):  return "X " + q["spec"][0]
def _cmd_scroll(q): return f"S {int(q['ticks'][0])}"
def _cmd_probe(q):  return "PROBE"

ROUTES = {
    "/hid/move": _cmd_move, "/hid/click": _cmd_click, "/hid/rclick": _cmd_rclick,
    "/hid/down": _cmd_down, "/hid/up": _cmd_up, "/hid/home": _cmd_home,
    "/hid/key": _cmd_key, "/hid/type": _cmd_type, "/hid/combo": _cmd_combo,
    "/hid/scroll": _cmd_scroll, "/hid/probe": _cmd_probe,
}


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/health":
            probe = BRIDGE.send("PROBE")
            return self._json(200, {"ok": probe["ok"], "port": BRIDGE.port,
                                    "pico_acking": probe["ok"], "probe": probe["ack"]})
        fn = ROUTES.get(u.path)
        if not fn:
            return self._json(404, {"ok": False, "error": "no such route", "path": u.path})
        try:
            cmd = fn(q)
        except (KeyError, ValueError, IndexError) as e:
            return self._json(400, {"ok": False, "error": f"bad params: {e}", "path": u.path})
        result = BRIDGE.send(cmd)
        return self._json(200 if result["ok"] else 502, result)

    do_GET = _handle
    do_POST = _handle

    def log_message(self, *a):
        pass  # quiet; the caller sees ACKs in the JSON


def main():
    global BRIDGE
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--serial", default="/dev/ttyAMA0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()

    BRIDGE = UartBridge(args.serial, args.baud, args.timeout)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"hid_bridge on http://{args.host}:{args.port}  serial={args.serial}@{args.baud}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
