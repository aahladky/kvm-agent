"""
pico_serial_log.py — capture the Pico's CircuitPython serial console to a timestamped file.

Run it on the host the Pico's USB data cable is plugged into (you said the laptop).
    pip install pyserial
    python pico_serial_log.py                 # auto-detect the Pico port
    python pico_serial_log.py /dev/ttyACM0     # or COM5 on Windows, if auto-detect misses

Logs every line with a wall-clock timestamp to pico_console_<ts>.log AND prints live, so
you can leave it running and see exactly when/why WiFi drops or a connection resets. The
updated firmware prints "WiFi dropped — reconnecting…" on a drop, which this will catch.
"""
import sys, time
try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pip install pyserial first")

def find_port():
    # CircuitPython on RP2040/RP2350 enumerates under Raspberry Pi (0x2E8A) or Adafruit (0x239A)
    for p in list_ports.comports():
        blob = f"{p.vid:04x}" if p.vid else ""
        desc = (p.description or "") + " " + (p.manufacturer or "")
        if blob in ("2e8a", "239a") or "circuitpython" in desc.lower() or "pico" in desc.lower():
            return p.device
    return None

port = sys.argv[1] if len(sys.argv) > 1 else find_port()
if not port:
    print("No Pico serial port auto-detected. Available ports:")
    for p in list_ports.comports():
        print(f"  {p.device}  vid={p.vid and hex(p.vid)}  {p.description}")
    sys.exit("Re-run with the port, e.g. python pico_serial_log.py COM5  (or /dev/ttyACM0)")

logname = "pico_console_" + time.strftime("%Y%m%d_%H%M%S") + ".log"
print(f"logging {port} @115200 -> {logname}  (Ctrl+C to stop)")
with serial.Serial(port, 115200, timeout=1) as ser, open(logname, "a", encoding="utf-8") as f:
    f.write(f"=== started {time.strftime('%Y-%m-%d %H:%M:%S')} on {port} ===\n"); f.flush()
    buf = b""
    while True:
        try:
            chunk = ser.read(256)
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    ts = time.strftime("%H:%M:%S")
                    text = line.decode("utf-8", "replace").rstrip("\r")
                    out = f"[{ts}] {text}"
                    print(out); f.write(out + "\n"); f.flush()
        except KeyboardInterrupt:
            break
        except Exception as e:
            ts = time.strftime("%H:%M:%S")
            f.write(f"[{ts}] <serial error: {e!r}>\n"); f.flush()
            time.sleep(1)
