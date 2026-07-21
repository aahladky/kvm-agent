"""
test_run_guard.py — OFFLINE test: a model-call failure (timeout/refused/5xx) ends the
TASK with a recorded verdict instead of propagating out of run() (review 2026-07-21
P0-2: an unguarded call_holo_full killed the current task's summary.json AND every
remaining battery task on a single network blip).

    python tests/test_run_guard.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_loop_holo

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)


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
    def log_step(self, step_idx, png, message, action, usage, wall_time_s, executed=True):
        self.steps.append({"step": step_idx, "action": action, "executed": executed})
    def finish(self, success, note=""):
        self.finished = (success, note)


def _boom(*a, **k):
    raise RuntimeError("simulated transport failure")

_saved = (agent_loop_holo.ENV, agent_loop_holo.call_holo_full, agent_loop_holo.RunRecorder)
recorders = []

class _TrackedRecorder(FakeRecorder):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        recorders.append(self)

agent_loop_holo.ENV = FakeEnv()
agent_loop_holo.call_holo_full = _boom
agent_loop_holo.RunRecorder = _TrackedRecorder
try:
    try:
        result = agent_loop_holo.run("simulate model outage", max_steps=10,
                                     confirm_first=0, record=True, tag="guardtest")
        propagated = False
    except Exception:
        result, propagated = None, True

    check("model-call failure does not propagate out of run()", not propagated)
    check("run() returns finished=False", result == {"finished": False, "answer_text": ""})
    rec = recorders[0] if recorders else None
    check("every failed call is logged as a non-executed step",
          rec is not None and len(rec.steps) == agent_loop_holo.STUCK_LIMIT
          and all(not s["executed"] for s in rec.steps))
    check("error steps carry the failure reason",
          rec is not None and all("model call failed" in (s["action"].get("error") or "")
                                  for s in rec.steps))
    check("recorder.finish(False) ran (summary.json gets written)",
          rec is not None and rec.finished is not None and rec.finished[0] is False)
finally:
    agent_loop_holo.ENV, agent_loop_holo.call_holo_full, agent_loop_holo.RunRecorder = _saved

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
