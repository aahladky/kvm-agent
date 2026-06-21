"""
_serial.py — talk to the Pico CircuitPython REPL on COM7. Sends Ctrl-C to break
into the REPL, then runs the one-liners given on argv (joined), reading all
console output. Used to inspect the LIVE enumerated usb_hid descriptor and fire
a direct send_report (no WiFi, no driver code in the loop).
"""
import serial, time, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = "COM7"
lines = sys.argv[1:]   # each arg = one REPL line

s = serial.Serial(PORT, 115200, timeout=0.3)
s.dtr = True
time.sleep(0.3)

def drain(t=0.6):
    end = time.time() + t
    out = b""
    while time.time() < end:
        n = s.in_waiting
        if n:
            out += s.read(n)
            end = time.time() + t
        else:
            time.sleep(0.05)
    return out.decode("utf-8", "replace")

# Ctrl-C: interrupt running code.py -> REPL
s.write(b"\x03")
time.sleep(0.4)
print("--- after Ctrl-C ---")
print(drain(1.0))

for ln in lines:
    s.write(ln.encode() + b"\r\n")
    time.sleep(0.15)
    out = drain(0.8)
    print(f">>> {ln}")
    if out.strip():
        print(out)
s.close()
