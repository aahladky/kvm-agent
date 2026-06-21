"""
_win_click_test.py — does the Pico's click register on WINDOWS?
Pico HID now injects into this Windows box. Right-click center-screen; if a
context menu appears, buttons register here -> the Mac failure is macOS-specific.
Captures the Windows screen directly via PIL ImageGrab (no capture card needed).
"""
import time
from PIL import ImageGrab
from r4_client import R4

r = R4()
before = ImageGrab.grab()
before.save("_win_before.png")
print("windows screen size:", before.size)

print("moving to center and RIGHT-clicking...")
r.move(960, 540)          # -> abs 50%,50% -> screen center regardless of res
time.sleep(0.6)
r.rclick()
time.sleep(1.2)
ImageGrab.grab().save("_win_after.png")
r.key("esc")              # close any menu
r.close()
print("saved _win_before.png and _win_after.png")
