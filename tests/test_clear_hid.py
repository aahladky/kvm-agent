"""
test_clear_hid.py — OFFLINE test: ApplianceClient.clear_hid() hits the bridge's
/hid/clear route and raises loudly on failure. The fake server mimics the REAL
bridge's failure shapes (2026-07-21 review P1-11: the previous fake returned
HTTP 200 with {"ok": false}, a shape the real bridge never produces — hid_bridge
returns 502/404/400, so the not-ok branch the old test exercised was unreachable
in production and the bridge's ack/error detail never reached the caller).

    python tests/test_clear_hid.py
"""
import sys, os, json, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from http.server import BaseHTTPRequestHandler, HTTPServer

from kvm_agent.hardware.appliance import ApplianceClient, ApplianceError


def _serve(fail_clear=False):
    hits = []

    class H(BaseHTTPRequestHandler):
        def _json(self, status, obj):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            hits.append(self.path)
            if self.path == "/hid/clear" and not fail_clear:
                self._json(200, {"ok": True, "ack": "CLR"})
            elif self.path == "/hid/clear":
                self._json(502, {"ok": False, "error": "pico error code=0x45"})
            else:
                self._json(404, {"ok": False, "error": "no such route"})
        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    return srv, url, hits


def test_clear_hid_posts_to_route():
    srv, url, hits = _serve()
    try:
        ApplianceClient(base_url=url).clear_hid()
        assert hits == ["/hid/clear"], "clear_hid posts to /hid/clear"
    finally:
        srv.shutdown()


def test_bridge_502_raises_with_error_detail():
    """The real bridge answers a failed HID command with 502 + an error body; the
    client must raise loudly AND carry the bridge's detail (not swallow it)."""
    srv, url, hits = _serve(fail_clear=True)
    try:
        err = None
        try:
            ApplianceClient(base_url=url).clear_hid()
        except ApplianceError as e:
            err = e
        assert err is not None, "bridge 502 raises ApplianceError"
        assert "transport error" in str(err), "non-2xx arrives as a transport error"
        assert "0x45" in str(err), "the bridge's error detail reaches the caller"
    finally:
        srv.shutdown()


def test_unknown_route_404_raises_with_detail():
    srv, url, hits = _serve()
    try:
        err = None
        try:
            ApplianceClient(base_url=url)._req("/hid/bogus")
        except ApplianceError as e:
            err = e
        assert err is not None, "unknown route raises ApplianceError"
        assert "no such route" in str(err), "the bridge's 404 detail reaches the caller"
    finally:
        srv.shutdown()


def test_type_scales_timeout_to_text_length():
    """Second review #5 (2026-07-21): the bridge types at ~60-90ms/char, so the
    default 5s timeout false-fires on ~65+ char segments (raising a 'transport
    error' while the Pi types to completion -- false failure plus target-side
    divergence). The timeout must scale with the text."""
    import kvm_agent.hardware.appliance as appl
    seen = []

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b'{"ok": true}'

    real = appl.urllib.request.urlopen
    appl.urllib.request.urlopen = lambda req, timeout=None: seen.append(timeout) or FakeResp()
    try:
        ApplianceClient(base_url="http://x").type("a" * 200)
    finally:
        appl.urllib.request.urlopen = real
    assert seen, "the type request was issued"
    assert seen[0] >= 5.0 + 0.12 * 200, \
        f"timeout scales with text length (200 chars), got {seen[0]}"


if __name__ == "__main__":
    import sys, traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except Exception:
            fails += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print("\n" + ("ALL PASS" if not fails else f"{fails} FAILED"))
    sys.exit(1 if fails else 0)
