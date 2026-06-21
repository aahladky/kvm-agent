"""Isolated reflash check: does alt+f4 (an F-key combo) close a window over HID now?
Open an EMPTY Notepad via Win+R (no typing -> no save prompt), send alt+f4, compare frames.
Before the reflash, combo('alt+f4') sent only Alt (F-keys dropped) -> no-op."""
import os, time
os.chdir(r"C:\Dev\vllm")
os.makedirs("_dbg", exist_ok=True)
import cv2, numpy as np
from pico_env import PicoEnv


def diff(a, b):
    A = cv2.imdecode(np.frombuffer(a, np.uint8), cv2.IMREAD_GRAYSCALE)
    B = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_GRAYSCALE)
    A = cv2.resize(A, (160, 90)); B = cv2.resize(B, (160, 90))
    return float(np.mean(np.abs(A.astype("int16") - B.astype("int16"))))


print("opening camera+pico (MSMF init ~25s)...", flush=True)
env = PicoEnv(cam_index=0, screen_size=(1920, 1080), show=False)


def grab(name):
    png = env.observe()["screenshot"]
    with open(os.path.join("_dbg", name), "wb") as f:
        f.write(png)
    return png


try:
    base = grab("fk_0_base.png"); time.sleep(0.5)
    print("launching notepad via win+r...", flush=True)
    env.r4.combo("win+r"); time.sleep(1.3)
    env.r4.type("notepad"); time.sleep(0.4)
    env.r4.key("enter"); time.sleep(2.5)
    opened = grab("fk_1_open.png")
    d_open = diff(base, opened)
    print(f"DIFF base->open    = {d_open:6.2f}   (HIGH = notepad appeared)", flush=True)

    print("sending alt+f4 (THE F-KEY TEST)...", flush=True)
    env.r4.combo("alt+f4"); time.sleep(2.0)
    closed = grab("fk_2_closed.png")
    d_close = diff(opened, closed)
    d_back = diff(base, closed)
    print(f"DIFF open->closed  = {d_close:6.2f}   (HIGH = window closed)", flush=True)
    print(f"DIFF base->closed  = {d_back:6.2f}   (LOW  = back to clean desktop)", flush=True)

    works = (d_open > 6.0) and (d_close > 6.0) and (d_back < 4.0)
    print("VERDICT:", "ALT+F4 WORKS - F-keys live after reflash" if works
          else "INCONCLUSIVE - inspect _dbg/fk_*.png", flush=True)
finally:
    env.close()
    print("FKEY TEST DONE, env closed", flush=True)
