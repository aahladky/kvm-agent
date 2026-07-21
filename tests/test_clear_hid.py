"""
test_clear_hid.py — OFFLINE test: ApplianceClient.clear_hid() hits the bridge's
/hid/clear route and raises loudly on a not-ok response (the all-keys-up wiring).

    python tests/test_clear_hid.py
"""
import sys, os, json, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from http.server import BaseHTTPRequestHandler, HTTPServer

from kvm_agent.hardware.appliance import ApplianceClient, ApplianceError


def _serve():
    hits = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append(self.path)
            ok = self.path == "/hid/clear"
            body = json.dumps({"ok": ok, "ack": "CLR" if ok else "no such route"}).encode()
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
    return srv, url, hits


def test_clear_hid_posts_to_route():
    srv, url, hits = _serve()
    try:
        ApplianceClient(base_url=url).clear_hid()
        assert hits == ["/hid/clear"], "clear_hid posts to /hid/clear"
    finally:
        srv.shutdown()


def test_not_ok_response_raises():
    srv, url, hits = _serve()
    try:
        try:
            ApplianceClient(base_url=url)._req("/hid/bogus")
            assert False, "not-ok response raises ApplianceError"
        except ApplianceError:
            pass
    finally:
        srv.shutdown()


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
