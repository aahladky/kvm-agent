"""
test_set_screen.py — OFFLINE test: the bridge's pixel->wire click scale gets synced.
(review 2026-07-21 P0-1: /hid/set_screen existed on both ends with no live caller, so
the bridge silently scaled every click from its process-lifetime default.)

    python tests/test_set_screen.py   (or pytest tests/test_set_screen.py)
"""
import sys, os, json, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from kvm_agent.hardware.appliance import ApplianceClient

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


_srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=_srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{_srv.server_address[1]}"


def test_client_posts_dims():
    hits.clear()
    ApplianceClient(base_url=URL).set_screen(1280, 720)
    assert hits == [("/hid/set_screen", {"w": "1280", "h": "720"})]


def test_boot_pushes_set_screen():
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

    saved = (agent_loop_holo.PicoEnv, agent_loop_holo.ENV)
    agent_loop_holo.PicoEnv = FakeEnv
    agent_loop_holo.ENV = None
    try:
        agent_loop_holo.boot(verify=False)   # the HID gate has its own test (test_boot_gate.py)
        assert agent_loop_holo.ENV.r4.calls == [("set_screen", 1920, 1080)], \
            "boot() must sync the bridge scale to the env's coordinate space"
    finally:
        agent_loop_holo.PicoEnv, agent_loop_holo.ENV = saved


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
