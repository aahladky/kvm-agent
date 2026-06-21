"""
_click_test4.py — does the LEFT button register? Drag on the desktop with the
button held; a selection rectangle should appear. Capture WHILE held.
  rectangle visible -> left button registers (and shows where)
  no rectangle      -> left button events are not registering
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
print("left-drag on desktop, capturing while held...")
r.move(300, 850)
time.sleep(0.4)
r.down()                 # press and HOLD
r.move(680, 1010)
time.sleep(0.3)
grab("_drag.png")        # button still held -> selection rect should be visible
r.up()                   # release
r.close()
print("done")
