"""
_hidtest.py — tight single-process HID test. Drives the Pico REPL over serial
AND reads the Windows cursor in the same process, so sends and measurements are
synchronized. Removes WiFi + driver code entirely. Tests:
  (1) does an absolute-mouse send_report move the Windows cursor, at several
      distinct positions (rules out 'first report dropped' / identical-report)?
  (2) does a button-down report register (GetAsyncKeyState LBUTTON)?
"""
import serial, time, sys, ctypes
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
u = ctypes.windll.user32

class P(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
def gp():
    p = P(); u.GetCursorPos(ctypes.byref(p)); return p.x, p.y

s = serial.Serial("COM7", 115200, timeout=0.3); s.dtr = True; time.sleep(0.3)
def rd(t=0.5):
    end=time.time()+t; o=b""
    while time.time()<end:
        n=s.in_waiting
        if n: o+=s.read(n); end=time.time()+t
        else: time.sleep(0.03)
    return o.decode("utf-8","replace")
def cmd(line, t=0.5):
    s.write(line.encode()+b"\r\n"); time.sleep(0.1); return rd(t)

s.write(b"\x03"); time.sleep(0.4); rd(0.8)            # Ctrl-C -> REPL
print("setup:", cmd("import usb_hid").strip()[-40:])
cmd("ds=list(usb_hid.devices)")
cmd("m=[d for d in ds if d.usage_page==1 and d.usage==2][0]")
cmd("b=bytearray(5)")
print("mouse caps:", cmd("print(m.usage_page, m.usage)").strip().splitlines()[-1] if cmd("print(1)") else "?")

def send_abs(ax, ay, btn=0):
    cmd("b[0]=%d" % btn)
    cmd("b[1]=%d" % (ax & 0xFF)); cmd("b[2]=%d" % ((ax>>8)&0xFF))
    cmd("b[3]=%d" % (ay & 0xFF)); cmd("b[4]=%d" % ((ay>>8)&0xFF))
    cmd("m.send_report(b)")

print("\n== positioning: park far away, send abs, read cursor ==")
for ax, ay in [(4000,4000),(28000,28000),(16384,16384),(1000,30000)]:
    u.SetCursorPos(50,50); time.sleep(0.1)
    send_abs(ax, ay)
    time.sleep(0.3)
    exp = (int(ax/32767*2560), int(ay/32767*1440))
    print(f"  abs({ax:5},{ay:5}) expect~{exp}  ->  cursor {gp()}")

print("\n== button: send LEFT down, check async key state ==")
send_abs(16384,16384, btn=0x01)        # tip/left down at center
time.sleep(0.05)
lb = u.GetAsyncKeyState(0x01) & 0x8000
print("  LBUTTON async-down after left-down report:", bool(lb))
send_abs(16384,16384, btn=0x00)        # release

s.close()
print("\ndone")
