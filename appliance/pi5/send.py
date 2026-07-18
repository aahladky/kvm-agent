#!/usr/bin/env python3
"""
send.py -- Pi 5 one-shot sender for the appliance HID bridge.

Sends ONE sequence-numbered command over the wired UART to the Pico and waits for
its ACK. Prints the ACK line; exit 0 iff it was "<seq> OK...". This is the
minimal controller-side primitive the Stage-5 HTTP bridge will wrap.

  python3 send.py "M 960,540"
  python3 send.py "PROBE"
  python3 send.py --timeout 3 "T hello world"

Port defaults to /dev/ttyAMA0 (the Pi 5 header UART on GPIO14/15 -- NOT
/dev/serial0, which points to a different UART on the Pi 5; see stage 1 notes).
"""
import argparse
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial missing: pip install pyserial")

# module-level monotonic-ish seq so repeated invocations don't collide within a session;
# across processes it restarts, which is fine -- the Pico only echoes the seq back, it
# doesn't require global uniqueness, just per-command matching.
_START_SEQ = int(time.time() * 1000) % 100000


def send_one(port, baud, cmd, timeout, seq):
    ser = serial.Serial(port, baud, timeout=timeout)
    try:
        ser.reset_input_buffer()
        ser.write(f"{seq} {cmd}\n".encode())
        t0 = time.time()
        line = ser.readline()
        dt_ms = (time.time() - t0) * 1000.0
    finally:
        ser.close()
    resp = line.decode(errors="replace").strip() if line else ""
    toks = resp.split()
    ok = len(toks) >= 2 and toks[0] == str(seq) and toks[1] == "OK"
    return ok, resp, dt_ms


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", help='command string, e.g. "M 960,540" or "PROBE"')
    ap.add_argument("--port", default="/dev/ttyAMA0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--timeout", type=float, default=3.0,
                    help="ACK timeout (s); typing a long string needs headroom")
    ap.add_argument("--seq", type=int, default=_START_SEQ)
    args = ap.parse_args()

    try:
        ok, resp, dt = send_one(args.port, args.baud, args.cmd, args.timeout, args.seq)
    except serial.SerialException as e:
        sys.exit(f"could not open {args.port}: {e}")

    if not resp:
        print(f"TIMEOUT (no ACK in {args.timeout}s)")
        return 1
    print(f"{resp}   [{dt:.1f}ms]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
