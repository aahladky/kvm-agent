"""Pinpoint the calc regression: does calc LAUNCH get detected? does TYPE land on calc?
Runs the measure steps manually with instrumentation; writes _dbg/calc_diag.json + frames."""
import os, time, json
os.chdir(r"C:\Dev\vllm")
os.makedirs("_dbg", exist_ok=True)
import cv2, numpy as np
from pico_env import PicoEnv
from cua_agent import make_agent
from executive import Executive, Verifier


def fdiff(a, b):
    A = cv2.imdecode(np.frombuffer(a, np.uint8), cv2.IMREAD_GRAYSCALE)
    B = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_GRAYSCALE)
    A = cv2.resize(A, (160, 90)); B = cv2.resize(B, (160, 90))
    return round(float(np.mean(np.abs(A.astype("int16") - B.astype("int16")))), 2)


res = {}
env = PicoEnv(cam_index=0, screen_size=(1920, 1080), show=False)
agent = make_agent("uitars", model="uitars-q4", history=1, temperature=0.0, screen_size=(1920, 1080))
ex = Executive(env, agent, verifier=Verifier())


def grab(n):
    p = env.observe()["screenshot"]
    with open(os.path.join("_dbg", n), "wb") as f:
        f.write(p)
    return p


try:
    ex.reset_clean(max_close=12)
    base = grab("cd_0_clean.png")

    # ---- notepad (baseline: this app worked in the alt+f4 test) ----
    res["launch_notepad_ok"] = ex.launch("notepad"); time.sleep(0.4)
    ex.type_text("milk, eggs, and bread"); time.sleep(0.5)
    f_np = grab("cd_1_notepad.png")
    res["diff_clean_to_notepad"] = fdiff(base, f_np)

    # ---- calc launch, ONE attempt, measured manually (no retry) ----
    before = ex.observe()
    env.r4.combo("win+r"); time.sleep(1.2)
    env.r4.type("calc"); time.sleep(0.4)
    env.r4.key("enter"); time.sleep(2.5)
    after = ex.observe()
    res["calc_launch_attempt0_diff"] = fdiff(before, after)   # <6.0 => launch() would FAIL/retry
    f_calc = grab("cd_2_calc.png")

    # ---- type the expression INTO calc (regardless of launch detection) ----
    before_type = ex.observe()
    ex.type_text("12+34"); time.sleep(0.6)
    f_typed = grab("cd_3_typed.png")
    res["diff_after_typing_expr"] = fdiff(before_type, f_typed)  # ~0 => keystrokes not landing on calc
    ex.tap("enter"); time.sleep(0.8)
    grab("cd_4_eq.png")
    res["display_read"] = ex.verifier.read_number(ex.observe())
    res["expected"] = "46"
finally:
    env.close()
    json.dump(res, open(os.path.join("_dbg", "calc_diag.json"), "w"), indent=2)
    print("CALC DIAG DONE")
    print(json.dumps(res, indent=2))
