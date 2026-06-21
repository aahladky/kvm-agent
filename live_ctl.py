"""
live_ctl.py — persistent interactive controller for the KVM rig.

Driven command-by-command from a python -i REPL (single-line calls, so REPL
pasting stays clean). Holds ONE camera + ONE R4 (Pico) connection open for the
whole session (closing cleanly on shutdown() so the firmware's blocking accept()
never gets wedged by a half-open socket).

Strength split this controller is built to exercise:
  - KEYBOARD-FIRST actions (hot/typ/tap) — deterministic, no grounding. Robust
    app launch via Win+R, typed text + typed arithmetic. This is what makes the
    multi-app task reliable.
  - UI-TARS executor (tars) — STATELESS single-step visual grounding for targets
    with no keyboard path. reset() every call => no history => no coord-mimicry.

Typical:
    from live_ctl import *
    boot()                       # open camera + Pico (slow: ~25s MSMF init)
    cap()                        # grab a fresh frame -> _dbg/live.png
    hot("win+r"); cap()          # launch primitive
    typ("notepad"); tap("enter"); cap()
    tars("click the Save button")    # propose (does NOT execute) -> review xy
    do()                         # execute the last proposed action
    shutdown()
"""
import os, time, base64
os.environ.setdefault("OPENAI_BASE_URL", "http://192.168.0.155:11434/v1")
os.environ.setdefault("OPENAI_API_KEY", "ollama")

import cv2
from io import BytesIO
from PIL import Image

DBG = r"C:\Dev\vllm\_dbg"
os.makedirs(DBG, exist_ok=True)

ENV = None          # PicoEnv (camera + R4)
AG = None           # UITARSAgent (executor)
LAST = {"png": None, "actions": None, "xy": None, "text": None}
EXECUTOR_MODEL = os.environ.get("EXECUTOR_MODEL", "uitars-q4")


def boot(executor=None):
    """Open camera + Pico + executor agent. Idempotent-ish; call once."""
    global ENV, AG
    from pico_env import PicoEnv
    from cua_agent import make_agent
    if ENV is None:
        ENV = PicoEnv(cam_index=0, screen_size=(1920, 1080), show=False)
    if AG is None:
        AG = make_agent("uitars", model=(executor or EXECUTOR_MODEL), history=1,
                        temperature=0.0, screen_size=(1920, 1080))
    print(f"[boot] ready. executor={getattr(AG,'model','?')}")
    return True


def _frame_png():
    return ENV.observe()["screenshot"]


def cap(name="live"):
    """Grab a fresh frame, save PNG to _dbg/<name>.png, return (path, WxH)."""
    png = _frame_png()
    LAST["png"] = png
    p = os.path.join(DBG, f"{name}.png")
    with open(p, "wb") as f:
        f.write(png)
    W, H = Image.open(BytesIO(png)).size
    print(f"[cap] {W}x{H} -> {p}")
    return p


def settle(s=1.0):
    time.sleep(s)


def hot(combo, s=1.5):
    """Keyboard combo, e.g. 'win+r', 'ctrl+s', 'alt+F4'. Settles s seconds."""
    ENV.r4.combo(combo); time.sleep(s)
    print(f"[hot] {combo}")


def typ(text, s=0.6):
    """Type a string over HID (US layout). No trailing Enter unless you add one."""
    ENV.r4.type(text); time.sleep(s)
    print(f"[typ] {text!r}")


def tap(key, s=0.8):
    """Tap a single named key: enter, esc, tab, backspace, up/down/left/right, etc."""
    ENV.r4.key(key); time.sleep(s)
    print(f"[tap] {key}")


def click(x, y, s=1.0):
    """Absolute click at screen px (1920x1080 space)."""
    ENV.r4.move(int(x), int(y)); ENV.r4.click(); time.sleep(s)
    print(f"[click] {x},{y}")


def tars(instruction, model=None):
    """STATELESS UI-TARS grounding for ONE instruction on the CURRENT frame.
    Proposes an action; does NOT execute. Review the xy (and crosshair via mark())
    then call do() to fire it."""
    global AG
    if model and (AG is None or getattr(AG, "model", None) != model):
        from cua_agent import make_agent
        AG = make_agent("uitars", model=model, history=1, temperature=0.0,
                        screen_size=(1920, 1080))
    png = _frame_png(); LAST["png"] = png
    AG.reset()
    t = time.time()
    text, actions = AG.predict(instruction, {"screenshot": png})
    dt = time.time() - t
    xy = None
    import re
    for a in actions:
        m = re.search(r"\((\d+),\s*(\d+)\)", a)
        if m:
            xy = (int(m.group(1)), int(m.group(2))); break
    LAST.update({"actions": actions, "xy": xy, "text": text})
    th = ""
    if text and "Thought:" in text:
        th = text.split("Thought:")[-1].split("Action:")[0].strip()[:160]
    print(f"[tars {dt:.1f}s] {instruction!r}\n   thought: {th}\n   actions: {actions}  xy={xy}")
    return actions


def mark(name="mark"):
    """Save the current frame with a crosshair at LAST xy, to eyeball grounding."""
    if LAST["png"] is None or LAST["xy"] is None:
        print("[mark] need a tars() proposal first"); return None
    import numpy as np
    arr = cv2.imdecode(np.frombuffer(LAST["png"], np.uint8), cv2.IMREAD_COLOR)
    x, y = LAST["xy"]
    cv2.drawMarker(arr, (x, y), (0, 0, 255), cv2.MARKER_CROSS, 40, 3)
    cv2.circle(arr, (x, y), 22, (0, 0, 255), 2)
    p = os.path.join(DBG, f"{name}.png")
    cv2.imwrite(p, arr)
    print(f"[mark] {x},{y} -> {p}")
    return p


def do(s=1.5):
    """Execute the LAST tars() proposal (the pyautogui action strings) via the Pico."""
    if not LAST["actions"]:
        print("[do] nothing proposed"); return
    for a in LAST["actions"]:
        if a in ("DONE", "FAIL", "WAIT", "ANSWER"):
            print(f"[do] control token {a} (skipped)"); continue
        ENV.controller.execute_python_command(a)
        print(f"[do] {a}")
    time.sleep(s)


def shutdown():
    """Close camera + Pico cleanly (important: leaves the firmware accept() healthy)."""
    global ENV, AG
    if ENV is not None:
        try:
            ENV.close()
        except Exception as e:
            print("[shutdown] env close err:", e)
    ENV = None; AG = None
    print("[shutdown] hardware released")
