"""
stage1_uart_echo.py -- Pico 2 W, Stage-1 bring-up for the Pi5+Pico appliance.

Proves the wired UART control link + line framing + sequence-numbered ACK, in
ISOLATION: no HID, no WiFi, no USB-gadget logic. Deploy by copying this to the
CIRCUITPY drive AS code.py (keep the existing boot.py -- its idle HID interface
does not interfere with the UART). Then wire to the Pi 5 and run
appliance/pi5/stage1_ping_test.py.

Wiring (3 wires, 3.3V both sides, NO level shifter, do NOT connect power rails):
  Pi5 GPIO14 TXD (hdr pin 8)  -> Pico GP1 RX (pin 2)     [cross: TX->RX]
  Pi5 GPIO15 RXD (hdr pin 10) -> Pico GP0 TX (pin 1)     [cross: RX<-TX]
  Pi5 GND        (hdr pin 6)  -> Pico GND   (pin 3)
The Pico is powered/flashed over its own USB; the GND wire is the shared signal
reference only.

Protocol (newline-framed ASCII), Stage 1 subset:
  host -> pico:  "<seq> PING [ignored...]\n"
  pico -> host:  "<seq> OK\n"                (seq echoed back)
  anything else: "<seq> ERR unknown_cmd\n"   (or "0 ERR <reason>" if unparseable)

This is deliberately the whole firmware for Stage 1 -- the full command set
(M/C/R/D/U/K/T/X/S/H + PROBE) and its ACK wrapper land in a later stage once the
link itself is proven.
"""
import board
import busio
import time

BAUD = 115200

# UART0 on the Pico's default pins: GP0 = TX, GP1 = RX.
uart = busio.UART(board.GP0, board.GP1, baudrate=BAUD, timeout=0.01,
                  receiver_buffer_size=256)

print("stage1_uart_echo up: UART0 GP0(TX)/GP1(RX) @", BAUD, "baud")

_buf = b""


def _reply(s):
    uart.write((s + "\n").encode())


while True:
    # Drain only what's actually waiting and act immediately -- do NOT call
    # read(N) with a fixed timeout, which blocks the full timeout waiting for N
    # bytes that never come and pins round-trip latency at ~2x the timeout
    # (measured 101ms with read(64)+50ms timeout in Stage 1). in_waiting + a 1ms
    # idle sleep drops it to near the wire time.
    n = uart.in_waiting
    if not n:
        time.sleep(0.001)
        continue
    chunk = uart.read(n)
    if not chunk:
        continue
    _buf += chunk
    while b"\n" in _buf:
        line, _buf = _buf.split(b"\n", 1)
        try:
            text = line.decode().strip()
        except Exception:
            _reply("0 ERR decode")
            continue
        if not text:
            continue
        parts = text.split(" ", 2)
        seq = parts[0]
        cmd = parts[1].upper() if len(parts) > 1 else ""
        if cmd == "PING":
            _reply(seq + " OK")
        else:
            _reply(seq + " ERR unknown_cmd")
