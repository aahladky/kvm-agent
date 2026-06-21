"""
_drv.py — fire a labeled HID sequence at the Pico and cross-check the OS cursor
position after each move via GetCursorPos. Firmware SCREEN_W/H=1920x1080 but this
display is 2560x1440, so predicted screen pos = sent * 2560/1920 = sent*1.3333.
"""
import ctypes, time, sys
from r4_client import R4

ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.183"
u = ctypes.windll.user32
SX, SY = 2560/1920.0, 1440/1080.0

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

def cursor():
    p = POINT(); u.GetCursorPos(ctypes.byref(p)); return p.x, p.y

r = R4(ip=ip)
print("connected", ip)

# CONTROL: prove the measurement harness works. Move cursor via Windows API.
u.SetCursorPos(300, 300); time.sleep(0.2)
print(f"  [control] SetCursorPos(300,300) -> GetCursorPos {cursor()}")
u.SetCursorPos(1500, 900); time.sleep(0.2)
print(f"  [control] SetCursorPos(1500,900) -> GetCursorPos {cursor()}")

def mv(x, y):
    r.move(x, y); time.sleep(0.35)
    print(f"  sent move({x},{y})  predict({int(x*SX)},{int(y*SY)})  actual cursor {cursor()}")

print("== positioning ==")
mv(960, 540)    # center
mv(480, 270)
mv(1440, 810)
mv(100, 100)

print("== clicks (watch _mon.log for L_DOWN/R_DOWN INJ) ==")
mv(700, 500)
print("  left click");  r.click();  time.sleep(0.4)
print("  right click"); r.rclick(); time.sleep(0.4)
print("  drag");        r.move(700,500); r.down(); time.sleep(0.1); r.move(1000,700); time.sleep(0.1); r.up()
time.sleep(0.3)
r.close()
print("done")
