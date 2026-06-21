"""
_click_test2.py — does a hardware click register on macOS AT ALL?
Click the menu-bar clock (top-right, forgiving target) -> Notification Center
should open. If it does, clicks work and the TV miss is a position problem.
If nothing opens, the click HID itself isn't registering.
"""
import time
import cv2
from r4_client import R4

def grab(path):
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    f = None
    t0 = time.time()
    while time.time() - t0 < 1.5:
        ok, fr = cap.read()
        if ok:
            f = fr
    cap.release()
    if f is not None:
        cv2.imwrite(path, f)
        print("saved", path)
    return f

r = R4()
print("clicking the menu-bar clock (top-right) ...")
r.move(1852, 12)
time.sleep(0.6)
r.down(); time.sleep(0.12); r.up()
time.sleep(1.2)
grab("_click_clock.png")
r.key("esc")          # close Notification Center if it opened
r.close()
print("done")
