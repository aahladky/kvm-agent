"""
agent_loop_holo.py — REPL-driven capture->ground->act loop for Holo3.1, built around
kvm_agent.models.holo (NOT the old EvoCUA/UI-TARS loop -- Holo emits a normalized
action dict via native tool-calling, not pyautogui code strings, so execution here maps
straight onto env.r4, bypassing the PicoPyAutoGUI exec-shim those older agents used).

STATUS: Phases I0-I5 done (see HOLO_INTEGRATION_PLAN.md) -- verified live against the rig
(VM target, SPICE-fullscreen capture, Pico HID over WiFi). ground()+do() (single action)
and run() (multi-step with real history threading -- see run()'s docstring for the
schema) have both landed a model-decided click correctly on a live target.

Modeled on live_ctl.py's proven propose-then-confirm shape (ground() proposes, do()
executes) so review-before-fire stays the default, per CLAUDE.md's "make failure loud"
discipline -- run()'s CONFIRM_FIRST gates the first N steps with a keypress preview, and
a stuck-detector (STUCK_LIMIT consecutive dropped/error actions) aborts instead of
burning the step budget.

Typical:
    from agent_loop_holo import *
    boot()                                  # open camera + Pico
    cap()                                   # grab a fresh frame -> _dbg/live.png
    ground("click the Save button")         # calls Holo, proposes an action, does NOT execute
    mark()                                  # eyeball the crosshair before firing
    do()                                    # execute the last proposed action
    run("open Notepad and type hello", max_steps=10)   # closed multi-step loop w/ history
"""
import os
import time
from io import BytesIO

from PIL import Image

from kvm_agent.config import CFG
from kvm_agent.hardware.env import PicoEnv
from kvm_agent.instrumentation import RunRecorder
from kvm_agent.models.holo import (
    call_holo, call_holo_full, observation_message, png_bytes_to_data_url, trim_to_last_n_images,
)

MAX_HISTORY_IMAGES = 3   # hub.hcompany.ai/agent-loop: "keep at most the last 3 screenshots"

DBG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_dbg")
os.makedirs(DBG, exist_ok=True)

CONFIRM_FIRST = 5   # gate the first N steps of run() with a keypress preview
STUCK_LIMIT = 3     # k consecutive dropped/error actions -> abort (make-failure-loud guard)

ENV = None
LAST = {"png": None, "action": None, "history": None}


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


def _frame_changed(png_a, png_b, threshold=3.0):
    """Cheap post-action signal for the tool-result message: did the screen visibly
    change? (mean absolute pixel difference, downscaled for speed). Not a correctness
    check -- a click that lands on the wrong-but-still-different element also reads as
    "changed" -- just distinguishes "something happened" from "silent no-op", which is
    exactly the gap hub.hcompany.ai/agent-loop's pitfall table calls out: a hardcoded
    tool-result string gives the model no way to learn an action didn't register."""
    import cv2
    import numpy as np
    a = cv2.imdecode(np.frombuffer(png_a, np.uint8), cv2.IMREAD_GRAYSCALE)
    b = cv2.imdecode(np.frombuffer(png_b, np.uint8), cv2.IMREAD_GRAYSCALE)
    a = cv2.resize(a, (160, 90))
    b = cv2.resize(b, (160, 90))
    return float(np.mean(np.abs(a.astype(int) - b.astype(int)))) > threshold


def do(s=1.5):
    """Execute the LAST ground() proposal via the Pico."""
    action = LAST.get("action")
    if not action:
        print("[do] nothing proposed")
        return
    _execute(action, settle_s=s)
    print(f"[do] {action}")


def run(instruction, max_steps=10, target="local", confirm_first=None, record=True, tag="run"):
    """Multi-step closed loop with real history threading: ground (against the accumulated
    history) -> confirm (first N steps) -> execute -> re-capture -> thread this step's
    observation + assistant tool-call + a tool-result message into history -> repeat.

    History format matches hub.hcompany.ai/agent-loop's documented function-calling chat
    layout (fetched and diffed against this file after Phase I5's first live run surfaced
    gaps -- see kvm_agent/models/holo.py's module docstring for the full list): each
    successful step appends
      {"role": "user", "content": [<observation>+image+</observation>]}   (this step's own)
      {"role": "assistant", "tool_calls": [...]}
      {"role": "tool", "tool_call_id": ..., "content": "<changed/unchanged>"}
    then trims to the last MAX_HISTORY_IMAGES screenshots. The task instruction is sent
    ONLY on step 0's observation turn (not every step, per the doc's loop example, which
    doesn't repeat it) -- later turns carry it via history. Tool-result content is a real
    frame-diff signal (_frame_changed), not a hardcoded "ok" -- docs flag exactly that gap
    as a cause of loops/forgetting.
    Steps that error (dropped/unparseable) are NOT threaded into history -- referencing a
    malformed tool_calls entry back to the model would confuse it more than a clean retry.
    tool_choice="required" (set in call_holo_full) should make these rare.

    confirm_first defaults to CONFIRM_FIRST; pass 0 to run unattended.

    record (default True, per PROJECT_GUIDANCE_holo.md §3.3 -- "unlogged runs are wasted
    runs") writes every step's pre-action frame, raw message, parsed action, token usage,
    and wall time to CFG.runs_dir/<tag>_<timestamp>/ via RunRecorder, plus a summary.json
    at the end. tag names the run directory (e.g. the task id in a battery).
    """
    confirm_first = CONFIRM_FIRST if confirm_first is None else confirm_first
    history = []
    LAST["history"] = history
    stuck = 0
    recorder = RunRecorder(tag, instruction, target=target,
                            meta={"max_steps": max_steps, "screen_size": CFG.screen_size}) if record else None
    for step in range(max_steps):
        png = _frame_png()
        LAST["png"] = png
        w, h = Image.open(BytesIO(png)).size
        data_url = png_bytes_to_data_url(png)
        step_instruction = instruction if step == 0 else ""
        t0 = time.time()
        action, message, usage = call_holo_full(step_instruction, data_url, w, h, target=target, history=history,
                                                  max_history_images=MAX_HISTORY_IMAGES)
        dt = time.time() - t0
        LAST["action"] = action
        print(f"[run {dt:.1f}s] step {step}: {action}")

        if action.get("action") == "error":
            stuck += 1
            if recorder:
                recorder.log_step(step, png, message, action, usage, dt, executed=False)
            print(f"[run] step {step}: dropped action ({stuck}/{STUCK_LIMIT})")
            if stuck >= STUCK_LIMIT:
                print("[run] stuck limit hit -- aborting")
                if recorder:
                    recorder.finish(False, note="stuck limit hit")
                return False
            continue
        stuck = 0

        if step < confirm_first:
            input(f"[run] step {step}: about to execute {action} -- Enter to confirm...")
        _execute(action)
        if recorder:
            recorder.log_step(step, png, message, action, usage, dt, executed=True)

        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            post_png = _frame_png()
            changed = _frame_changed(png, post_png)
            tool_content = f"Action executed. Screen {'changed.' if changed else 'did not visibly change.'}"
            history.append(observation_message(data_url, step_instruction))
            history.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls})
            history.append({"role": "tool", "tool_call_id": tool_calls[0].get("id", "call_0"), "content": tool_content})
            trim_to_last_n_images(history, n=MAX_HISTORY_IMAGES)

        if action.get("action") == "finished":
            print(f"[run] finished: {action.get('text')!r}")
            if recorder:
                recorder.finish(True, note=action.get("text", ""))
            return True
    print("[run] max_steps reached without finishing")
    if recorder:
        recorder.finish(False, note="max_steps reached")
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
