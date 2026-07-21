"""
test_set_screen.py — OFFLINE test: the bridge's pixel->wire click scale gets synced.
(review 2026-07-21 P0-1: /hid/set_screen existed on both ends with no live caller, so
the bridge silently scaled every click from its process-lifetime default.)

  1. ApplianceClient.set_screen() hits /hid/set_screen with the w/h params.
  2. agent_loop_holo.boot() pushes the env's screen dims to the bridge on bring-up.

    python tests/test_set_screen.py
"""
import sys, os, json, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from kvm_agent.hardware.appliance import ApplianceClient

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)

hits = []

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        u = urlparse(self.path)
        hits.append((u.path, {k: v[0] for k, v in parse_qs(u.query).items()}))
        body = json.dumps({"ok": True, "ack": "SCREEN"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass

srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}"

# --- 1. client method posts the dims ---
ApplianceClient(base_url=url).set_screen(1280, 720)
check("set_screen posts to /hid/set_screen", hits and hits[0][0] == "/hid/set_screen")
check("set_screen carries w/h", hits and hits[0][1] == {"w": "1280", "h": "720"})

# --- 2. boot() wires it: bridge scale synced to the env's coordinate space ---
import agent_loop_holo

class FakeR4:
    def __init__(self):
        self.calls = []
    def set_screen(self, w, h):
        self.calls.append(("set_screen", w, h))

class FakeEnv:
    def __init__(self, cam_index=0, screen_size=(1920, 1080), show=False):
        self.screen_width, self.screen_height = screen_size
        self.r4 = FakeR4()

_real_env_cls, _real_env = agent_loop_holo.PicoEnv, agent_loop_holo.ENV
agent_loop_holo.PicoEnv = FakeEnv
agent_loop_holo.ENV = None
try:
    agent_loop_holo.boot()
    calls = agent_loop_holo.ENV.r4.calls
    check("boot() pushes set_screen once", calls == [("set_screen", 1920, 1080)])
finally:
    agent_loop_holo.PicoEnv, agent_loop_holo.ENV = _real_env_cls, _real_env

srv.shutdown()
print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
