"""
_p2_win_test.py — clean click test on Pico #2 (Windows, .224).
Identical firmware to #1. Win+D (keyboard) -> right-click (button2) -> left-drag
(button1). Whatever registers tells us buttons work on Windows.
"""
import time
from PIL import ImageGrab
from r4_client import R4

r = R4(ip="192.168.0.224")
print("Win+D (clean desktop, also tests keyboard)...")
r.combo("win+d")
time.sleep(1.5)
ImageGrab.grab().save("_p2_desktop.png")

print("RIGHT-click center...")
r.move(960, 540); time.sleep(0.5)
r.rclick(); time.sleep(1.0)
ImageGrab.grab().save("_p2_rclick.png")
r.key("esc"); time.sleep(0.4)

print("LEFT-drag rubber-band (capture while held)...")
r.move(700, 400); time.sleep(0.4)
r.down(); r.move(1300, 800); time.sleep(0.4)
ImageGrab.grab().save("_p2_ldrag.png")
r.up(); time.sleep(0.3)

r.combo("win+d")   # restore
r.close()
print("saved _p2_desktop.png _p2_rclick.png _p2_ldrag.png")
