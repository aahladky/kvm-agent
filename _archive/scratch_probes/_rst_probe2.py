"""
_rst_probe2.py — discriminate 'Pico reboots/crashes' vs 'server stays up but
drops the connection'. Reconnect repeatedly; measure connect latency and
whether the conn resets instantly or blocks (stays alive). A reboot shows up
as a multi-second window where connect FAILS (port down during reboot).
"""
import socket, time

IP, PORT = "192.168.0.183", 8000

for i in range(10):
    t0 = time.monotonic()
    try:
        s = socket.create_connection((IP, PORT), timeout=3)
        dt = (time.monotonic() - t0) * 1000
        s.settimeout(1.5)
        try:
            d = s.recv(64)
            outcome = "FIN(clean)" if d == b"" else f"data:{d!r}"
        except socket.timeout:
            outcome = "ALIVE (recv blocked 1.5s, no reset)"
        except Exception as e:
            outcome = f"RESET:{type(e).__name__}"
        try:
            s.close()
        except Exception:
            pass
        print(f"#{i}  connect_ok {dt:5.0f}ms  -> {outcome}")
    except Exception as e:
        dt = (time.monotonic() - t0) * 1000
        print(f"#{i}  connect_FAIL {dt:5.0f}ms  {type(e).__name__}: {e}")
    time.sleep(0.4)
