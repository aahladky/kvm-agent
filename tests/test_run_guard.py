"""
test_run_guard.py — OFFLINE test: a model-call failure (timeout/refused/5xx) ends the
TASK with a recorded verdict instead of propagating out of run() (review 2026-07-21
P0-2: an unguarded call_holo_full killed the current task's summary.json AND every
remaining battery task on a single network blip).

    python tests/test_run_guard.py   (or pytest tests/test_run_guard.py)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_loop_holo


class FakeCam:
    def model_input_jpeg(self):
        return b"\xff\xd8fakejpeg"


class FakeEnv:
    screen_width, screen_height = 1920, 1080
    cam = FakeCam()
    def observe(self):
        return {"screenshot": b"\x89PNGfake"}


class FakeRecorder:
    def __init__(self, *a, **k):
        self.steps = []
        self.finished = None
        _RECORDERS.append(self)
    def log_step(self, step_idx, png, message, action, usage, wall_time_s, executed=True,
                 stalled=False):
        self.steps.append({"step": step_idx, "action": action, "executed": executed})
    def finish(self, success, note=""):
        self.finished = (success, note)


_RECORDERS = []


def _boom(*a, **k):
    raise RuntimeError("simulated transport failure")


def test_model_call_failure_is_contained():
    saved = (agent_loop_holo.ENV, agent_loop_holo.call_holo_full, agent_loop_holo.RunRecorder)
    _RECORDERS.clear()
    agent_loop_holo.ENV = FakeEnv()
    agent_loop_holo.call_holo_full = _boom
    agent_loop_holo.RunRecorder = FakeRecorder
    try:
        result = agent_loop_holo.run("simulate model outage", max_steps=10,
                                     confirm_first=0, record=True, tag="guardtest")
    finally:
        agent_loop_holo.ENV, agent_loop_holo.call_holo_full, agent_loop_holo.RunRecorder = saved

    assert result == {"finished": False, "answer_text": ""}, "run() must return, not raise"
    rec = _RECORDERS[0]
    assert len(rec.steps) == agent_loop_holo.STUCK_LIMIT, "each failed call logged as a step"
    assert all(not s["executed"] for s in rec.steps)
    assert all("model call failed" in (s["action"].get("error") or "") for s in rec.steps), \
        "error steps carry the failure reason"
    assert rec.finished is not None and rec.finished[0] is False, \
        "recorder.finish(False) ran (summary.json gets written)"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
