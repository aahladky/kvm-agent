"""
test_clear_hid.py — OFFLINE test: ApplianceClient.clear_hid() hits the bridge's
/hid/clear route and raises loudly on a not-ok response (the all-keys-up wiring).

    python tests/test_clear_hid.py
"""
import sys, os, json, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from http.server import BaseHTTPRequestHandler, HTTPServer

from kvm_agent.hardware.appliance import ApplianceClient, ApplianceError

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)

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

ApplianceClient(base_url=url).clear_hid()
check("clear_hid posts to /hid/clear", hits == ["/hid/clear"])

try:
    ApplianceClient(base_url=url)._req("/hid/bogus")
    check("not-ok response raises ApplianceError", False)
except ApplianceError:
    check("not-ok response raises ApplianceError", True)

srv.shutdown()
print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
