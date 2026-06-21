"""HID is back. Clean the target, then run the ISOLATED alt+f4 (F-key) test from a
clean desktop — the actual purpose of the reflash."""
import os, time
os.chdir(r"C:\Dev\vllm")
os.makedirs("_dbg", exist_ok=True)
import cv2, numpy as np
from pico_env import PicoEnv
from cua_agent import make_agent
from executive import Executive, Verifier


def diff(a, b):
    A = cv2.imdecode(np.frombuffer(a, np.uint8), cv2.IMREAD_GRAYSCALE)
    B = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_GRAYSCALE)
    A = cv2.resize(A, (160, 90)); B = cv2.resize(B, (160, 90))
    return float(np.mean(np.abs(A.astype("int16") - B.astype("int16"))))


print("opening camera+pico+agent...", flush=True)
env = PicoEnv(cam_index=0, screen_size=(1920, 1080), show=False)
agent = make_agent("uitars", model="uitars-q4", history=1, temperature=0.0, screen_size=(1920, 1080))
ex = Executive(env, agent, verifier=Verifier())


def grab(n):
    p = env.observe()["screenshot"]
    with open(os.path.join("_dbg", n), "wb") as f:
        f.write(p)
    return p


try:
    env.r4.key("esc"); time.sleep(0.5); env.r4.key("esc"); time.sleep(0.5)
    grab("rc_0_before.png")
    print("reset_clean (vision-gated)...", flush=True)
    st = ex.reset_clean(max_close=12)
    print("  reset_clean ->", st, flush=True)
    clean = grab("rc_1_clean.png")

    print("ISOLATED alt+f4 test: launch EMPTY notepad...", flush=True)
    env.r4.combo("win+r"); time.sleep(1.3)
    env.r4.type("notepad"); time.sleep(0.4)
    env.r4.key("enter"); time.sleep(2.5)
    opened = grab("rc_2_open.png"); d_open = diff(clean, opened)
    print(f"  diff clean->open   = {d_open:6.2f}  (HIGH = notepad opened)", flush=True)

    print("  send alt+f4 (THE F-KEY FIX) ...", flush=True)
    env.r4.combo("alt+f4"); time.sleep(2.0)
    closed = grab("rc_3_closed.png")
    d_close = diff(opened, closed); d_back = diff(clean, closed)
    print(f"  diff open->closed  = {d_close:6.2f}  (HIGH = alt+f4 closed it)", flush=True)
    print(f"  diff clean->closed = {d_back:6.2f}  (LOW  = back to clean)", flush=True)
    works = d_open > 6 and d_close > 6 and d_back < 4
    print("VERDICT:", "ALT+F4 WORKS - F-keys live after reflash" if works else "INSPECT rc_*.png", flush=True)
finally:
    env.close(); print("RECHECK DONE", flush=True)
