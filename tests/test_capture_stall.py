"""
test_capture_stall.py — OFFLINE test: a violated freshness floor is SURFACED, not
swallowed (review 2026-07-21 P0-3: a wait_newer timeout used to be a print, then the
loop diffed a possibly pre-action frame -- the finding-#6 class, reopened silently).

  1. _execute() returns True when the capture never advances past the fire.
  2. run() tells the MODEL (tool_output WARNING), records `stalled` on the step, and
     aborts after STALL_ABORT_LIMIT consecutive stalls -- even with
     no_progress_abort=False (rig fault, not model behavior).
  3. verify_hid blames the CAMERA, not the HID, when capture delivers no frames.

    python tests/test_capture_stall.py
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import cv2

import agent_loop_holo
from kvm_agent.hardware import target

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)

FRAME = np.full((270, 480, 3), 128, np.uint8)
PNG = cv2.imencode(".png", FRAME)[1].tobytes()


class StalledCam:
    """Frames exist (read() works) but the pipeline never advances past an action."""
    seq = 7
    def read(self):
        return FRAME
    def wait_newer(self, seq, timeout_s):
        raise TimeoutError(f"no frame newer than seq={seq}")
    def model_input_jpeg(self):
        return b"\xff\xd8fakejpeg"

class FakeR4:
    def __getattr__(self, name):
        return lambda *a, **k: None

class StalledEnv:
    screen_width, screen_height = 1920, 1080
    cam = StalledCam()
    r4 = FakeR4()
    def observe(self):
        return {"screenshot": PNG}


_saved = (agent_loop_holo.ENV, agent_loop_holo.call_holo_full, agent_loop_holo.RunRecorder)
agent_loop_holo.ENV = StalledEnv()

# --- 1. _execute surfaces the stall ---
stalled = agent_loop_holo._execute({"action": "left_click", "coordinate": [10, 10]},
                                   settle_s=0.05)
check("_execute returns True on a stalled capture", stalled is True)
check("_execute returns False for plan-only actions (nothing to stall)",
      agent_loop_holo._execute({"action": "update_plan", "goals": []}) is False)

# --- 2. run(): model warned, step recorded as stalled, rig-fault abort ---
def fake_call(*a, **k):
    step = {"actions": [{"action": "left_click", "coordinate": [10, 10]}],
            "note": None, "thought": None}
    return step, {"content": json.dumps(step)}, None

class FakeRecorder:
    def __init__(self, *a, **k):
        self.steps, self.finished = [], None
        recorders.append(self)
    def log_step(self, step_idx, png, message, action, usage, wall_time_s, executed=True,
                 stalled=False):
        self.steps.append({"executed": executed, "stalled": stalled})
    def finish(self, success, note=""):
        self.finished = (success, note)

recorders = []
agent_loop_holo.call_holo_full = fake_call
agent_loop_holo.RunRecorder = FakeRecorder
try:
    result = agent_loop_holo.run("stall storm", max_steps=10, confirm_first=0,
                                 record=True, tag="stalltest", no_progress_abort=False)
    check("stall abort ends the run unfinished", result == {"finished": False, "answer_text": ""})
    rec = recorders[0]
    check("aborts after STALL_ABORT_LIMIT consecutive stalled steps (even with "
          "no_progress_abort=False)", len(rec.steps) == agent_loop_holo.STALL_ABORT_LIMIT)
    check("every step was recorded as stalled", all(s["stalled"] for s in rec.steps))
    check("verdict names the rig fault",
          rec.finished == (False, "capture pipeline stalled"))
    warned = [m for m in agent_loop_holo.LAST["history"]
              if isinstance(m.get("content"), str) and "WARNING: capture stalled" in m["content"]]
    check("the model was told via tool_output", len(warned) == agent_loop_holo.STALL_ABORT_LIMIT)
finally:
    agent_loop_holo.ENV, agent_loop_holo.call_holo_full, agent_loop_holo.RunRecorder = _saved

# --- 3. verify_hid: dead capture blames the camera, not the HID ---
class DeadCam:
    def read(self):
        return None
    def png_bytes(self, full_res=False):
        return PNG

ok, detail = target.verify_hid(FakeR4(), DeadCam(), settle_s=0.1, attempts=1)
check("verify_hid fails closed on a dead capture", ok is False)
check("verify_hid's diagnosis names the camera", "capture" in detail and "HID" in detail)

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
