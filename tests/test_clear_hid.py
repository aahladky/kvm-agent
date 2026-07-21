"""
test_clear_hid.py — OFFLINE test: ApplianceClient.clear_hid() hits the bridge's
/hid/clear route and raises loudly on a not-ok response (the all-keys-up wiring).

NOTE (review 2026-07-21 P1): the real bridge signals failure via HTTP 502/404/400,
which _req surfaces as a "transport error" ApplianceError -- the 200+ok:false shape
tested here can only come from a non-bridge server. Both paths must raise; both are
covered below.

    python tests/test_clear_hid.py   (or pytest tests/test_clear_hid.py)
"""
import sys, os, json, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from http.server import BaseHTTPRequestHandler, HTTPServer

from kvm_agent.hardware.appliance import ApplianceClient, ApplianceError

hits = []


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        hits.append(self.path)
        if self.path == "/hid/clear":
            body = json.dumps({"ok": True, "ack": "CLR"}).encode()
            self.send_response(200)
        elif self.path == "/hid/notok":
            body = json.dumps({"ok": False, "error": "declined"}).encode()
            self.send_response(200)
        else:
            # the REAL bridge's shape for a bad route: HTTP 404 (hid_bridge.py)
            body = json.dumps({"ok": False, "error": "no such route"}).encode()
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


_srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=_srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{_srv.server_address[1]}"


def test_clear_hid_posts_to_route():
    hits.clear()
    ApplianceClient(base_url=URL).clear_hid()
    assert hits == ["/hid/clear"]


def test_ok_false_raises():
    try:
        ApplianceClient(base_url=URL)._req("/hid/notok")
        assert False, "ok:false must raise ApplianceError"
    except ApplianceError:
        pass


def test_http_error_status_raises():
    # what the real bridge actually sends on failure (502/404/400)
    try:
        ApplianceClient(base_url=URL)._req("/hid/bogus")
        assert False, "HTTP error status must raise ApplianceError"
    except ApplianceError:
        pass


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
