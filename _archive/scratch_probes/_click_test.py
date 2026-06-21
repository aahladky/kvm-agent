"""
_click_test.py — isolate the click. The model's fast click() (30ms hold) didn't
launch TV though the cursor was on it. Test a DELIBERATE long-hold click via the
D/U commands (no Pico edit needed), then capture the Mac to see if TV launched.
"""
import time
import cv2
from r4_client import R4

TVX, TVY = 997, 1038          # where the model grounded the TV icon

def grab(path):
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    f = None
    t0 = time.time()
    while time.time() - t0 < 1.5:
        ok, frame = cap.read()
        if ok:
            f = frame
    cap.release()
    if f is not None:
        cv2.imwrite(path, f)
        print("saved", path, f.shape[1], "x", f.shape[0])
    return f

r = R4()
print("R4 connected; moving to TV icon and long-hold clicking...")
r.move(TVX, TVY)
time.sleep(0.6)               # dwell so the cursor is settled on the icon
r.down()                      # button down
time.sleep(0.18)              # 180ms hold (vs 30ms in click())
r.up()                        # button up
print("clicked (180ms hold). waiting 2.5s for TV to launch...")
time.sleep(2.5)
r.close()
grab("_click_after.png")
print("done")
