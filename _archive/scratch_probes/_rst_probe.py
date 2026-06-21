"""
_rst_probe.py — isolate WHERE the Pico connection dies. Pure client-side,
does NOT touch r4_client. Phase A sends nothing (is an idle conn reset?);
Phase B sends one harmless 'H' (home) and watches.
"""
import socket, time

IP, PORT = "192.168.0.183", 8000

s = socket.create_connection((IP, PORT), timeout=5)
print("connected to", IP, PORT)
s.settimeout(1)

def watch(label, seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            d = s.recv(64)
            if d == b"":
                print(f"  [{label}] peer closed CLEANLY (FIN), no data"); return "fin"
            print(f"  [{label}] recv: {d!r}"); return "data"
        except socket.timeout:
            continue
        except Exception as e:
            print(f"  [{label}] RESET/err: {type(e).__name__}: {e}"); return "reset"
    print(f"  [{label}] survived {seconds}s, connection still open, no data")
    return "alive"

print("Phase A: idle 3s, NO command sent...")
a = watch("idle", 3)

print("Phase B: sending one 'H' (home)...")
try:
    s.sendall(b"H\n")
    print("  sendall ok")
except Exception as e:
    print("  sendall FAILED:", type(e).__name__, e)
b = watch("after-H", 3)

try:
    s.close()
except Exception:
    pass
print(f"\nRESULT  idle={a!r}  after_H={b!r}")
