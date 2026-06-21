"""Isolate HID liveness after the reflash: is MOUSE reaching the target? is KEYBOARD?
TCP to the Pico is confirmed up, but win+r had no effect. Test each HID channel with a
clearly-visible result and frame-diff it."""
import os, time
os.chdir(r"C:\Dev\vllm")
os.makedirs("_dbg", exist_ok=True)
import cv2, numpy as np
from pico_env import PicoEnv


def diff(a, b):
    A = cv2.imdecode(np.frombuffer(a, np.uint8), cv2.IMREAD_GRAYSCALE)
    B = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_GRAYSCALE)
    full = float(np.mean(np.abs(A.astype("int16") - B.astype("int16"))))
    a2 = cv2.resize(A, (160, 90)); b2 = cv2.resize(B, (160, 90))
    small = float(np.mean(np.abs(a2.astype("int16") - b2.astype("int16"))))
    return full, small


print("opening camera+pico...", flush=True)
env = PicoEnv(cam_index=0, screen_size=(1920, 1080), show=False)


def grab(name):
    png = env.observe()["screenshot"]
    with open(os.path.join("_dbg", name), "wb") as f:
        f.write(png)
    return png


try:
    d0 = grab("hd_0_base.png"); time.sleep(0.4)

    # ---- MOUSE test: right-click center -> desktop context menu should appear ----
    print("MOUSE: home + move(960,540) + right-click ...", flush=True)
    print("  r4.home ->", repr(env.r4.home()), flush=True)
    print("  r4.move ->", repr(env.r4.move(960, 540)), flush=True)
    time.sleep(0.4)
    print("  r4.rclick ->", repr(env.r4.rclick()), flush=True)
    time.sleep(1.5)
    d1 = grab("hd_1_rclick.png")
    f1, s1 = diff(d0, d1)
    print(f"  DIFF base->rclick  full={f1:6.2f} small={s1:6.2f}  (HIGH = menu shown = MOUSE OK)", flush=True)
    # dismiss any menu with a left-click far from center
    env.r4.move(220, 430); env.r4.click(); time.sleep(1.0)
    d2 = grab("hd_2_dismiss.png")

    # ---- KEYBOARD test: win+r -> Run dialog should appear ----
    print("KEYBOARD: combo('win+r') ...", flush=True)
    print("  r4.combo ->", repr(env.r4.combo("win+r")), flush=True)
    time.sleep(1.6)
    d3 = grab("hd_3_winr.png")
    f3, s3 = diff(d2, d3)
    print(f"  DIFF dismiss->winr full={f3:6.2f} small={s3:6.2f}  (HIGH = Run box = KEYBOARD OK)", flush=True)
    if s3 > 6.0:
        env.r4.key("esc"); time.sleep(0.6)

    print("SUMMARY: mouse_ok=%s keyboard_ok=%s" % (s1 > 6.0, s3 > 6.0), flush=True)
finally:
    env.close()
    print("HID DIAG DONE, env closed", flush=True)
