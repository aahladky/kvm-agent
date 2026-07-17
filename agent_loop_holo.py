"""
agent_loop_holo.py — REPL-driven capture->ground->act loop for Holo3.1, built around
kvm_agent.models.holo (NOT the old EvoCUA/UI-TARS loop -- Holo emits a normalized
action dict via native tool-calling, not pyautogui code strings, so execution here maps
straight onto env.r4, bypassing the PicoPyAutoGUI exec-shim those older agents used).

STATUS: Phase I0 skeleton (see HOLO_INTEGRATION_PLAN.md). Imports and structure only --
NOT yet exercised against the live rig. Sequencing per the plan: I2 proves the Pico HID
seam alone, I3 closes the coordinate space, I4 is the first live ground()+do() step, I5
adds real multi-step history threading (see the TODO in run() below -- that contract is
NOT implemented yet, this loop currently grounds each step single-shot / no history).

Modeled on live_ctl.py's proven propose-then-confirm shape (ground() proposes, do()
executes) so review-before-fire stays the default, per CLAUDE.md's "make failure loud"
discipline -- run()'s CONFIRM_FIRST gates the first N steps with a keypress preview, and
a stuck-detector (STUCK_LIMIT consecutive dropped/error actions) aborts instead of
burning the step budget.

Typical (once the rig is live):
    from agent_loop_holo import *
    boot()                                  # open camera + Pico
    cap()                                   # grab a fresh frame -> _dbg/live.png
    ground("click the Save button")         # calls Holo, proposes an action, does NOT execute
    mark()                                  # eyeball the crosshair before firing
    do()                                    # execute the last proposed action
    run("open Notepad and type hello", max_steps=10)   # closed single-step-history loop
"""
import os
import time
from io import BytesIO

from PIL import Image

from kvm_agent.config import CFG
from kvm_agent.hardware.env import PicoEnv
from kvm_agent.models.holo import call_holo, png_bytes_to_data_url

DBG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_dbg")
os.makedirs(DBG, exist_ok=True)

CONFIRM_FIRST = 5   # gate the first N steps of run() with a keypress preview
STUCK_LIMIT = 3     # k consecutive dropped/error actions -> abort (make-failure-loud guard)

ENV = None
LAST = {"png": None, "action": None}


def boot():
    """Open camera + Pico. Idempotent-ish; call once."""
    global ENV
    if ENV is None:
        ENV = PicoEnv(cam_index=CFG.cam_index, screen_size=CFG.screen_size, show=False)
    print(f"[boot] ready. holo target=local ({CFG.holo_local_url}, model={CFG.holo_model})")
    return True


def _frame_png():
    return ENV.observe()["screenshot"]


def cap(name="live"):
    """Grab a fresh frame, save PNG to _dbg/<name>.png, return the path."""
    png = _frame_png()
    LAST["png"] = png
    p = os.path.join(DBG, f"{name}.png")
    with open(p, "wb") as f:
        f.write(png)
    w, h = Image.open(BytesIO(png)).size
    print(f"[cap] {w}x{h} -> {p}")
    return p


def ground(instruction, target="local"):
    """ONE Holo call against the CURRENT frame. Proposes an action; does NOT execute --
    review it (mark() to eyeball the crosshair) then call do() to fire it."""
    png = _frame_png()
    LAST["png"] = png
    w, h = Image.open(BytesIO(png)).size
    data_url = png_bytes_to_data_url(png)
    t0 = time.time()
    action = call_holo(instruction, data_url, w, h, target=target)
    dt = time.time() - t0
    LAST["action"] = action
    print(f"[ground {dt:.1f}s] {instruction!r} -> {action}")
    return action


def mark(name="mark"):
    """Save the current frame with a crosshair at LAST action's coordinate, to eyeball
    grounding before firing (the plan's #1 risk: the three-way coordinate agreement)."""
    action = LAST.get("action")
    if LAST["png"] is None or not action or "coordinate" not in action:
        print("[mark] need a ground() proposal with a coordinate first")
        return None
    import cv2
    import numpy as np
    arr = cv2.imdecode(np.frombuffer(LAST["png"], np.uint8), cv2.IMREAD_COLOR)
    x, y = (int(v) for v in action["coordinate"])
    cv2.drawMarker(arr, (x, y), (0, 0, 255), cv2.MARKER_CROSS, 40, 3)
    cv2.circle(arr, (x, y), 22, (0, 0, 255), 2)
    p = os.path.join(DBG, f"{name}.png")
    cv2.imwrite(p, arr)
    print(f"[mark] {x},{y} -> {p}")
    return p


def _execute(action, settle_s=1.5):
    """Fire ONE normalized Holo action dict via the Pico. Maps directly onto env.r4 --
    NOT the pyautogui-code exec shim (that's the EvoCUA/UI-TARS action representation;
    Holo's is a structured dict, see kvm_agent/models/holo.py's module docstring)."""
    kind = action.get("action")
    if kind == "left_click":
        x, y = (int(v) for v in action["coordinate"])
        ENV.r4.move(x, y)
        ENV.r4.click()
    elif kind == "type":
        ENV.r4.type(action.get("text", ""))
        if action.get("press_enter"):
            ENV.r4.key("enter")
    elif kind == "scroll":
        direction = action.get("direction")
        if direction == "up":
            ENV.r4.scroll(3)
        elif direction == "down":
            ENV.r4.scroll(-3)
        else:
            # v5 firmware wheel is single-axis vertical only (see boot.py/code.py) --
            # left/right have no real mapping. No-op, loud, rather than a wrong guess.
            print(f"[execute] scroll direction={direction!r} not supported by current "
                  f"firmware (vertical wheel only) -- no-op")
    elif kind == "drag":
        x1, y1 = (int(v) for v in action["start"])
        x2, y2 = (int(v) for v in action["coordinate"])
        ENV.r4.drag(x1, y1, x2, y2)
    elif kind in ("finished", "error"):
        pass    # nothing to execute; run() handles these as loop-terminal/stuck
    else:
        print(f"[execute] unknown action kind {kind!r} -- no-op")
    time.sleep(settle_s)


def do(s=1.5):
    """Execute the LAST ground() proposal via the Pico."""
    action = LAST.get("action")
    if not action:
        print("[do] nothing proposed")
        return
    _execute(action, settle_s=s)
    print(f"[do] {action}")


def run(instruction, max_steps=10, target="local", confirm_first=None):
    """Single-step-at-a-time closed loop: ground -> confirm (first N steps) -> execute
    -> re-capture -> repeat.

    TODO (Phase I5, not implemented here): thread real multi-turn history into each
    ground() call -- the assistant tool-call + a tool-result message per step, per the
    chat-layout convention in docs/FORMAT_NOTES_holo.md -- so Holo can see its own prior
    actions instead of grounding each step from scratch. Today every step is a fresh,
    history-less call; this is intentionally honest rather than a fake/incorrect history
    stub. Phase I5's acceptance criterion is specifically building this out and proving
    it on a 2-3 step task.

    UNTESTED against the live rig (Phase I0 skeleton) -- exercised starting at I4/I5.
    confirm_first defaults to CONFIRM_FIRST; pass 0 to run unattended (only once I4/I5
    have proven the loop out on this rig).
    """
    confirm_first = CONFIRM_FIRST if confirm_first is None else confirm_first
    stuck = 0
    for step in range(max_steps):
        action = ground(instruction, target=target)
        if action.get("action") == "error":
            stuck += 1
            print(f"[run] step {step}: dropped action ({stuck}/{STUCK_LIMIT})")
            if stuck >= STUCK_LIMIT:
                print("[run] stuck limit hit -- aborting")
                return False
            continue
        stuck = 0
        if step < confirm_first:
            input(f"[run] step {step}: about to execute {action} -- Enter to confirm...")
        _execute(action)
        if action.get("action") == "finished":
            print(f"[run] finished: {action.get('text')!r}")
            return True
    print("[run] max_steps reached without finishing")
    return False


def shutdown():
    """Close camera + Pico cleanly (leaves the firmware accept() healthy)."""
    global ENV
    if ENV is not None:
        try:
            ENV.close()
        except Exception as e:
            print("[shutdown] env close err:", e)
    ENV = None
    print("[shutdown] hardware released")
