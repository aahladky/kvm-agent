"""diag_hindsight.py — probe the local Hindsight server before we build a client.

Confirms reachability from the desktop, dumps the recall/retain/reflect endpoints + request
schemas from /openapi.json (authoritative), and runs a live recall + list against the bank.

    python tools\diag_hindsight.py
    python tools\diag_hindsight.py http://192.168.0.184:8888 TARS
"""
import sys, json, urllib.request, urllib.error

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://192.168.0.184:8888").rstrip("/")
BANK = sys.argv[2] if len(sys.argv) > 2 else "TARS"


def req(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:600]


print(f"BASE={BASE}  BANK={BANK}")

# 1) OpenAPI: the exact endpoints + request bodies for recall/retain/reflect ------------------
try:
    st, body = req("GET", "/openapi.json")
    spec = json.loads(body)
    print("\n/openapi.json OK. endpoints of interest:")
    for p, ops in sorted(spec.get("paths", {}).items()):
        if any(k in p for k in ("recall", "reflect", "/memories")):
            for m, o in ops.items():
                if m.lower() not in ("get", "post", "patch", "put", "delete"):
                    continue
                ref = (o.get("requestBody", {}).get("content", {})
                        .get("application/json", {}).get("schema", {}))
                print(f"  {m.upper():5} {p}   body={json.dumps(ref)[:160]}")
    schemas = spec.get("components", {}).get("schemas", {})
    for name, s in schemas.items():
        if any(k in name for k in ("Retain", "RecallRequest", "ReflectRequest")):
            print(f"\n  schema {name}: {json.dumps(s)[:600]}")
except Exception as e:
    print("openapi error:", repr(e)[:200])

# 2) live recall against the bank -------------------------------------------------------------
st, body = req("POST", f"/v1/default/banks/{BANK}/memories/recall",
               {"query": "how do I set the default web browser on Windows 10"})
print(f"\nRECALL -> {st}\n{body[:900]}")

# 3) list what's already in the bank ----------------------------------------------------------
st, body = req("GET", f"/v1/default/banks/{BANK}/memories/list?limit=10")
print(f"\nLIST -> {st}\n{body[:900]}")

# 4) optional: seed a few world facts, then recall to see the POPULATED result shape ----------
if "seed" in sys.argv:
    import time
    facts = [
        "On Windows, install an application from a terminal using winget: launch cmd, then run "
        "'winget install --silent --accept-package-agreements --accept-source-agreements "
        "<PackageId>' (for Firefox the PackageId is Mozilla.Firefox).",
        "On the Windows 10 test machine, the current default web browser is Google Chrome, not "
        "Microsoft Edge.",
        "On the Windows 10 test machine the Firefox Start-menu shortcut is broken: it points to a "
        "moved private_browsing.exe and opens a 'Problem with Shortcut' dialog instead of "
        "launching Firefox.",
        "To set the default web browser on Windows 10: open ms-settings:defaultapps, scroll down "
        "to the 'Web browser' row, click the current browser tile, then pick the new browser in "
        "the flyout.",
    ]
    for f in facts:
        st, body = req("POST", f"/v1/default/banks/{BANK}/memories",
                       {"items": [{"content": f, "context": "kvm-agent windows automation"}]},
                       timeout=60)
        print(f"\nRETAIN -> {st}\n{body[:300]}")
    print("\n...waiting for extraction..."); time.sleep(8)
    for q in ("set the default web browser to Firefox on Windows 10",
              "launch Firefox on the test machine"):
        st, body = req("POST", f"/v1/default/banks/{BANK}/memories/recall", {"query": q})
        print(f"\nRECALL {q!r} -> {st}\n{body[:1400]}")
