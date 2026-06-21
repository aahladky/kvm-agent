"""
_click_test3.py — does a button register, and is the cursor where we aim?
Right-click on the desktop: a context menu appears AT the real cursor position.
 - menu appears at ~(700,750)  -> buttons register, mapping is 1:1
 - menu appears but OFFSET     -> buttons register, capture != screen (fix mapping)
 - no menu at all              -> button events not registering (HID issue)
"""
import time
import cv2
from r4_client import R4

AIMX, AIMY = 700, 750

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
print(f"right-clicking the desktop at aim=({AIMX},{AIMY}) ...")
r.move(AIMX, AIMY)
time.sleep(0.6)
r.rclick()
time.sleep(1.0)
grab("_click_rmenu.png")
r.key("esc")
r.close()
print("done")
