#!/usr/bin/env python3
"""
stage1_ping_test.py -- Pi 5 side of Stage-1 bring-up for the Pi5+Pico appliance.

Fires N sequence-numbered PINGs over the wired UART and verifies each ACK comes
back with the MATCHING seq within a timeout. Reports round-trip latency and
counts ok / mismatch / timeout. Proves the wired control link + framing + ACK in
ISOLATION -- no HID, no capture, no VM. This is the whole point of Stage 1: the
current WiFi transport could NEVER report a dropped command; here a drop is a
loud TIMEOUT and a desync is a loud MISMATCH.

Setup:
  - `pip install pyserial` (or `sudo apt install python3-serial`).
  - PORT GOTCHA (measured 2026-07-18 on Pi 5 / Trixie): the header UART on
    GPIO14/15 (pins 8/10) is **/dev/ttyAMA0**, NOT /dev/serial0. On this Pi 5
    /dev/serial0 symlinks to ttyAMA10 (the SoC PL011), which is a DIFFERENT UART
    not wired to the header -- writing to it silently goes nowhere. Default here
    is /dev/ttyAMA0; if pings all time out, scan the candidates: for each
    /dev/ttyAMA*, write a labelled ping and see which one the Pico actually
    receives (that's the header UART). `dtparam=uart0=on` in config.txt already
    muxes GPIO14/15 to it (verify: `pinctrl get 14,15` -> a4 TXD0/RXD0).

Run:
  python3 stage1_ping_test.py                       # defaults: /dev/ttyAMA0, 200 pings
  python3 stage1_ping_test.py --port /dev/ttyAMA0 --n 500 --baud 115200

Exit code 0 iff every ping got a correct, in-order ACK.
"""
import argparse
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial missing: pip install pyserial  (or apt install python3-serial)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="/dev/ttyAMA0",
                    help="UART device (default /dev/ttyAMA0 -- the Pi 5 header UART; "
                         "NOT /dev/serial0, which points elsewhere on Pi 5)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--n", type=int, default=200, help="number of pings")
    ap.add_argument("--timeout", type=float, default=0.5, help="per-ping ACK timeout (s)")
    args = ap.parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=args.timeout)
    except serial.SerialException as e:
        sys.exit(f"could not open {args.port}: {e}\n"
                 f"  is the header UART enabled? on Pi 5 the header UART is "
                 f"/dev/ttyAMA0 (not /dev/serial0). check `pinctrl get 14,15`")

    time.sleep(0.2)
    ser.reset_input_buffer()

    ok = mismatch = timeouts = 0
    lats = []
    for seq in range(1, args.n + 1):
        ser.reset_input_buffer()          # drop any late/stale ACK -> clean per-ping window
        t0 = time.time()
        ser.write(f"{seq} PING\n".encode())
        line = ser.readline()             # up to args.timeout
        dt_ms = (time.time() - t0) * 1000.0
        if not line:
            timeouts += 1
            print(f"seq {seq}: TIMEOUT (no ACK in {args.timeout}s)")
            continue
        resp = line.decode(errors="replace").strip()
        got = resp.split(" ", 1)[0]
        if got == str(seq) and "OK" in resp:
            ok += 1
            lats.append(dt_ms)
        else:
            mismatch += 1
            print(f"seq {seq}: MISMATCH -> {resp!r}")

    ser.close()

    print(f"\n{ok}/{args.n} OK, {mismatch} mismatch, {timeouts} timeout")
    if lats:
        lats.sort()
        print(f"round-trip ms: min {lats[0]:.1f}  "
              f"median {lats[len(lats)//2]:.1f}  max {lats[-1]:.1f}")
    ok_all = (ok == args.n)
    print("STAGE 1:", "PASS" if ok_all else "FAIL")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
