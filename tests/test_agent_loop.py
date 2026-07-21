"""
test_agent_loop.py — OFFLINE tests for agent_loop_holo's run() failure containment,
no-progress guards, _execute pointer handling, and the env bring-up screen-size push
(fake env/recorder/model; no hardware, no network).

Covers the 2026-07-21 repo-review batch-1 fixes:
    P0-1  PicoEnv pushes the real screen size to the bridge via set_screen
    P0-2  a model-call exception is contained as a dropped step, never kills the run
    P1-7  planning-only (update_plan) steps don't count toward the frozen-screen abort
    P1-8  drag_to re-asserts the start position before button-down

    python tests/test_agent_loop.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

import agent_loop_holo as al
import kvm_agent.hardware.env as env_mod

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)


def _png(v):
    arr = np.full((270, 480, 3), v, np.uint8)
    ok, buf = cv2.imencode(".png", arr)
    return buf.tobytes()

FRAME = np.zeros((270, 480, 3), np.uint8)


class FakeCam:
    seq = 1
    def read(self): return FRAME
    def wait_newer(self, seq, timeout_s): return FRAME, seq + 1
    def model_input_jpeg(self):
        ok, buf = cv2.imencode(".jpg", FRAME)
        return buf.tobytes()


class FakeR4:
    def __init__(self):
        self.calls = []
    def move(self, x, y): self.calls.append(("move", x, y))
    def down(self): self.calls.append(("down",))
    def up(self): self.calls.append(("up",))
    def clear_hid(self): self.calls.append(("clear_hid",))
    def set_screen(self, w, h): self.calls.append(("set_screen", w, h))


class FakeEnv:
    def __init__(self):
        self.cam = FakeCam()
        self.r4 = FakeR4()
        self.screen_width, self.screen_height = 1920, 1080
    def observe(self):
        return {"screenshot": _png(0)}


class FakeRecorder:
    instances = []
    def __init__(self, tag, goal, target=None, meta=None):
        self.tag = tag
        self.steps = []
        self.finished = None
        FakeRecorder.instances.append(self)
    def log_step(self, *a, **k):
        self.steps.append((a, k))
    def finish(self, success, note=""):
        self.finished = (success, note)
        return {}


# --- P0-1: env bring-up syncs the bridge's pixel->wire scale to the real screen ---
r4 = FakeR4()
real_camera, real_make = env_mod.Camera, env_mod.make_hid_client
env_mod.Camera = lambda *a, **k: FakeCam()
env_mod.make_hid_client = lambda: r4
try:
    env_mod.PicoEnv(cam_index=0, screen_size=(1280, 720), show=False)
finally:
    env_mod.Camera, env_mod.make_hid_client = real_camera, real_make
check("env bring-up pushes the real screen size to the bridge",
      ("set_screen", 1280, 720) in r4.calls)
check("set_screen rides on the same connect as clear_hid",
      r4.calls[:2] == [("clear_hid",), ("set_screen", 1280, 720)])


def _patch_run(model_fn):
    saved = (al.ENV, al.call_holo_full, al.RunRecorder)
    al.ENV = FakeEnv()
    al.call_holo_full = model_fn
    al.RunRecorder = FakeRecorder
    FakeRecorder.instances.clear()
    return saved


def _restore_run(saved):
    al.ENV, al.call_holo_full, al.RunRecorder = saved


# --- P0-2: a model-call exception is contained (never propagates, recorder finishes) ---
def always_fail(*a, **k):
    raise TimeoutError("simulated 180s API timeout")

saved = _patch_run(always_fail)
try:
    result = al.run("do something", max_steps=5, confirm_first=0, tag="t_modfail")
finally:
    _restore_run(saved)
rec = FakeRecorder.instances[-1]
check("model-call failure does not propagate out of run()", result["finished"] is False)
check("recorder.finish still runs on persistent model-call failure",
      rec.finished is not None)
check("model-call failures count as stuck steps and abort at the limit",
      rec.finished == (False, "stuck limit hit"))


calls = {"n": 0}
def fail_then_finish(*a, **k):
    calls["n"] += 1
    if calls["n"] == 1:
        raise RuntimeError("simulated transient API error")
    return ({"actions": [{"action": "finished", "text": "all done"}], "note": None},
            {"content": "{}"}, {"prompt_tokens": 1})

saved = _patch_run(fail_then_finish)
try:
    result = al.run("do something", max_steps=5, confirm_first=0, tag="t_recover")
finally:
    _restore_run(saved)
rec = FakeRecorder.instances[-1]
check("a single model-call failure does not kill the run", result["finished"] is True)
check("run still returns the model's answer after a transient failure",
      result["answer_text"] == "all done")
check("a recovered run records success", rec.finished is not None and rec.finished[0] is True)


# --- P1-7: planning-only steps never trip the frozen-screen abort ---
def plan_only(*a, **k):
    return ({"actions": [{"action": "update_plan",
                          "goals": [{"title": "g", "status": "running"}]}],
             "note": None},
            {"content": "{}"}, {})

saved = _patch_run(plan_only)
try:
    result = al.run("plan a lot", max_steps=5, confirm_first=0, tag="t_plan")
finally:
    _restore_run(saved)
rec = FakeRecorder.instances[-1]
check("planning-only steps never trip the frozen-screen abort",
      rec.finished is not None and "max_steps" in rec.finished[1])
check("planning-only run uses its full step budget",
      result == {"finished": False, "answer_text": ""})


# --- P1-8: drag_to re-asserts the tracked start position before button-down ---
fake = FakeEnv()
saved_env, saved_cur = al.ENV, al.CURSOR["pos"]
al.ENV = fake
al.CURSOR["pos"] = (100, 100)
try:
    al._execute({"action": "drag_to", "coordinate": [500, 500]}, settle_s=0.1)
finally:
    al.ENV = saved_env
    al.CURSOR["pos"] = saved_cur
check("drag_to re-asserts the start position before button-down",
      fake.r4.calls[:2] == [("move", 100, 100), ("down",)])
check("drag_to still ends at the target and releases",
      fake.r4.calls[-2:] == [("move", 500, 500), ("up",)])


print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
