"""
_win_click_test2.py — definitive Windows click test on a clean desktop.
Win+D -> right-click center (desktop menu) -> left-drag (rubber-band box).
Either visual appearing proves the button registers on Windows.
"""
import time
from PIL import ImageGrab
from r4_client import R4

r = R4()
print("show desktop (Win+D)...")
r.combo("win+d")
time.sleep(1.5)
ImageGrab.grab().save("_win2_desktop.png")

print("RIGHT-click center...")
r.move(960, 540)
time.sleep(0.5)
r.rclick()
time.sleep(1.0)
ImageGrab.grab().save("_win2_rclick.png")
r.key("esc")
time.sleep(0.4)

print("LEFT-drag (rubber-band), capturing while held...")
r.move(700, 400)
time.sleep(0.4)
r.down()
r.move(1300, 800)
time.sleep(0.4)
ImageGrab.grab().save("_win2_ldrag.png")
r.up()
time.sleep(0.3)

r.combo("win+d")     # restore windows
r.close()
print("saved _win2_rclick.png and _win2_ldrag.png")
