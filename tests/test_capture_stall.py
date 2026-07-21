"""
test_capture_stall.py — OFFLINE test: a violated freshness floor is SURFACED, not
swallowed (review 2026-07-21 P0-3: a wait_newer timeout used to be a print, then the
loop diffed a possibly pre-action frame -- the finding-#6 class, reopened silently).

    python tests/test_capture_stall.py   (or pytest tests/test_capture_stall.py)
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import cv2

import agent_loop_holo
from kvm_agent.hardware import target

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


class FakeRecorder:
    def __init__(self, *a, **k):
        self.steps, self.finished = [], None
        _RECORDERS.append(self)
    def log_step(self, step_idx, png, message, action, usage, wall_time_s, executed=True,
                 stalled=False):
        self.steps.append({"executed": executed, "stalled": stalled})
    def finish(self, success, note=""):
        self.finished = (success, note)


_RECORDERS = []


def _fake_call(*a, **k):
    step = {"actions": [{"action": "left_click", "coordinate": [10, 10]}],
            "note": None, "thought": None}
    return step, {"content": json.dumps(step)}, None


def test_execute_surfaces_stall():
    saved = agent_loop_holo.ENV
    agent_loop_holo.ENV = StalledEnv()
    try:
        stalled = agent_loop_holo._execute({"action": "left_click", "coordinate": [10, 10]},
                                           settle_s=0.05)
        assert stalled is True, "_execute must return the freshness-floor violation"
        assert agent_loop_holo._execute({"action": "update_plan", "goals": []}) is False, \
            "plan-only actions have nothing to stall"
    finally:
        agent_loop_holo.ENV = saved


def test_run_warns_model_records_stall_and_aborts():
    saved = (agent_loop_holo.ENV, agent_loop_holo.call_holo_full, agent_loop_holo.RunRecorder)
    _RECORDERS.clear()
    agent_loop_holo.ENV = StalledEnv()
    agent_loop_holo.call_holo_full = _fake_call
    agent_loop_holo.RunRecorder = FakeRecorder
    try:
        result = agent_loop_holo.run("stall storm", max_steps=10, confirm_first=0,
                                     record=True, tag="stalltest", no_progress_abort=False)
    finally:
        agent_loop_holo.ENV, agent_loop_holo.call_holo_full, agent_loop_holo.RunRecorder = saved

    assert result == {"finished": False, "answer_text": ""}
    rec = _RECORDERS[0]
    # rig-fault abort fires even with no_progress_abort=False (that flag masks
    # MODEL-behavior guards; a stalled capture is our pipeline failing)
    assert len(rec.steps) == agent_loop_holo.STALL_ABORT_LIMIT
    assert all(s["stalled"] for s in rec.steps), "every step recorded as stalled"
    assert rec.finished == (False, "capture pipeline stalled"), "verdict names the rig fault"
    warned = [m for m in agent_loop_holo.LAST["history"]
              if isinstance(m.get("content"), str) and "WARNING: capture stalled" in m["content"]]
    assert len(warned) == agent_loop_holo.STALL_ABORT_LIMIT, "the model was told via tool_output"


def test_verify_hid_blames_camera_not_hid():
    class DeadCam:
        def read(self):
            return None
        def png_bytes(self, full_res=False):
            return PNG

    ok, detail = target.verify_hid(FakeR4(), DeadCam(), settle_s=0.1, attempts=1)
    assert ok is False, "fails closed on a dead capture"
    assert "capture" in detail and "HID" in detail, "diagnosis names the camera"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
