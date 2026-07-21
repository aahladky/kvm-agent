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
from kvm_agent.hardware.appliance import ApplianceError


def _png(v):
    arr = np.full((270, 480, 3), v, np.uint8)
    ok, buf = cv2.imencode(".png", arr)
    return buf.tobytes()

FRAME = np.zeros((270, 480, 3), np.uint8)


class FakeCam:
    def __init__(self, frame=None):
        self._frame = frame if frame is not None else FRAME
        self._seq = 0
    @property
    def seq(self):
        self._seq += 1   # every access = a fresh capture (keeps seq-aware settle honest)
        return self._seq
    def read(self): return self._frame
    def wait_newer(self, seq, timeout_s): return self._frame, seq + 1
    def model_input_jpeg(self):
        ok, buf = cv2.imencode(".jpg", self._frame)
        return buf.tobytes()


class FakeR4:
    def __init__(self):
        self.calls = []
    def move(self, x, y): self.calls.append(("move", x, y))
    def click(self): self.calls.append(("click",))
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
    def close(self):
        pass


class StallCam(FakeCam):
    """wait_newer always times out — the capture-stall class (review P0-3)."""
    def wait_newer(self, seq, timeout_s):
        raise TimeoutError(f"no frame newer than seq={seq} within {timeout_s}s")


class FakeRecorder:
    instances = []
    def __init__(self, tag, goal, target=None, meta=None):
        self.tag = tag
        self.meta = meta or {}
        self.steps = []
        self.finished = None
        FakeRecorder.instances.append(self)
    def log_step(self, *a, **k):
        self.steps.append((a, k))
    def finish(self, success, note=""):
        self.finished = (success, note)
        return {}


def _patch_run(model_fn):
    saved = (al.ENV, al.call_holo_full, al.RunRecorder)
    al.ENV = FakeEnv()
    al.call_holo_full = model_fn
    al.RunRecorder = FakeRecorder
    FakeRecorder.instances.clear()
    return saved


def _restore_run(saved):
    al.ENV, al.call_holo_full, al.RunRecorder = saved


# --- P0-1: env bring-up syncs the bridge's pixel->wire scale to the real screen ---
def test_p0_1_env_bringup_pushes_screen_size():
    r4 = FakeR4()
    real_camera, real_make = env_mod.Camera, env_mod.make_hid_client
    env_mod.Camera = lambda *a, **k: FakeCam(np.zeros((720, 1280, 3), np.uint8))
    env_mod.make_hid_client = lambda: r4
    try:
        env_mod.PicoEnv(cam_index=0, screen_size=(1280, 720), show=False)
    finally:
        env_mod.Camera, env_mod.make_hid_client = real_camera, real_make
    assert ("set_screen", 1280, 720) in r4.calls, \
        "env bring-up pushes the real screen size to the bridge"
    assert r4.calls[:2] == [("clear_hid",), ("set_screen", 1280, 720)], \
        "set_screen rides on the same connect as clear_hid"


# --- P0-2: a model-call exception is contained (never propagates, recorder finishes) ---
def test_p0_2_model_call_containment():
    def always_fail(*a, **k):
        raise TimeoutError("simulated 180s API timeout")

    saved = _patch_run(always_fail)
    try:
        result = al.run("do something", max_steps=5, confirm_first=0, tag="t_modfail")
    finally:
        _restore_run(saved)
    rec = FakeRecorder.instances[-1]
    assert result["finished"] is False, "model-call failure does not propagate out of run()"
    assert rec.finished is not None, \
        "recorder.finish still runs on persistent model-call failure"
    assert rec.finished == (False, "stuck limit hit"), \
        "model-call failures count as stuck steps and abort at the limit"

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
    assert result["finished"] is True, "a single model-call failure does not kill the run"
    assert result["answer_text"] == "all done", \
        "run still returns the model's answer after a transient failure"
    assert rec.finished is not None and rec.finished[0] is True, \
        "a recovered run records success"


# --- P1-7: planning-only steps never trip the frozen-screen abort ---
def test_p1_7_planning_only_steps_no_freeze_abort():
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
    assert rec.finished is not None and "max_steps" in rec.finished[1], \
        "planning-only steps never trip the frozen-screen abort"
    assert result == {"finished": False, "answer_text": ""}, \
        "planning-only run uses its full step budget"


# --- P1-8: drag_to re-asserts the tracked start position before button-down ---
def test_p1_8_drag_to_reasserts_start():
    fake = FakeEnv()
    saved_env, saved_cur = al.ENV, al.CURSOR["pos"]
    al.ENV = fake
    al.CURSOR["pos"] = (100, 100)
    try:
        al._execute({"action": "drag_to", "coordinate": [500, 500]}, settle_s=0.1)
    finally:
        al.ENV = saved_env
        al.CURSOR["pos"] = saved_cur
    assert fake.r4.calls[:2] == [("move", 100, 100), ("down",)], \
        "drag_to re-asserts the start position before button-down"
    assert fake.r4.calls[-2:] == [("move", 500, 500), ("up",)], \
        "drag_to still ends at the target and releases"


# --- P0-3: a capture stall is surfaced, not swallowed ---
def test_p0_3_capture_stall_surfaced():
    fake = FakeEnv()
    fake.cam = StallCam()
    saved_env = al.ENV
    al.ENV = fake
    try:
        info = al._execute({"action": "move_to", "coordinate": [5, 5]}, settle_s=0.3)
    finally:
        al.ENV = saved_env
    assert info is not None and info.get("stalled") and "seq=" in info["stalled"], \
        "_execute reports the stall instead of only printing it"
    assert info.get("settle") == "stable", "_execute reports the settle status"

    def click_then_finish(*a, **k):
        return ({"actions": [{"action": "left_click", "coordinate": [10, 10]},
                             {"action": "finished", "text": "done"}], "note": None},
                {"content": "{}"}, {})

    fake = FakeEnv()
    fake.cam = StallCam()
    saved = (al.ENV, al.call_holo_full, al.RunRecorder)
    al.ENV = fake
    al.call_holo_full = click_then_finish
    al.RunRecorder = FakeRecorder
    FakeRecorder.instances.clear()
    try:
        result = al.run("click and finish", max_steps=3, confirm_first=0, tag="t_stall")
    finally:
        al.ENV, al.call_holo_full, al.RunRecorder = saved
    assert result["finished"] is True, "a stalled run still completes"
    tool_outputs = [m["content"] for m in al.LAST["history"]
                    if m.get("role") == "user" and isinstance(m.get("content"), str)
                    and m["content"].startswith("<tool_output")]
    assert any("WARNING" in t and "stale" in t for t in tool_outputs), \
        "the stall reaches the model's <tool_output>"
    rec = FakeRecorder.instances[-1]
    logged_step = rec.steps[0][0][3]   # log_step(step_i, png, message, step, ...)
    assert any("stalled" in w for w in logged_step.get("warnings", [])), \
        "the stall reaches the recorder (step warnings)"


# --- P0-4: boot() runs the camera-verified HID gate by default ---
def test_p0_4_boot_hid_gate():
    import kvm_agent.hardware.target as target_mod

    saved = (al.ENV, al.PicoEnv, target_mod.verify_hid)
    try:
        al.PicoEnv = lambda *a, **k: FakeEnv()
        target_mod.verify_hid = lambda r4, cam, **k: (False, "mouse NOT delivering (test)")
        al.ENV = None
        raised = False
        try:
            al.boot()
        except RuntimeError:
            raised = True
        assert raised, "boot() fails closed when the HID gate fails"
        assert al.ENV is None, "boot() tears down the env after a gate failure"

        gate_calls = {"n": 0}
        def passing_gate(r4, cam, **k):
            gate_calls["n"] += 1
            return True, "hid ok (test)"
        target_mod.verify_hid = passing_gate
        al.ENV = None
        assert al.boot() is True, "boot() returns True when the gate passes"
        assert gate_calls["n"] == 1, "the gate actually ran"
        al.ENV = None
        al.boot(verify=False)
        assert gate_calls["n"] == 1, "boot(verify=False) skips the gate"
    finally:
        al.ENV, al.PicoEnv, target_mod.verify_hid = saved


def test_p1_9_camera_bringup_failure_is_catchable():
    """P1-9 (2026-07-21 review): Camera raised SystemExit on bring-up failure, which
    sails past `except Exception` in any embedding caller (battery, future server).
    It must raise a catchable RuntimeError instead."""
    import time
    import types

    class FakeCap:
        def set(self, *a): pass
        def read(self):
            time.sleep(0.01)   # don't busy-spin the capture thread
            return False, None
        def release(self): pass

    fake_cv2 = types.SimpleNamespace(
        VideoCapture=lambda *a, **k: FakeCap(),
        CAP_PROP_FRAME_WIDTH=0, CAP_PROP_FRAME_HEIGHT=0, CAP_PROP_BUFFERSIZE=0)
    real_cv2 = env_mod.cv2
    env_mod.cv2 = fake_cv2
    try:
        raised = None
        try:
            env_mod.Camera(0, bringup_timeout_s=0.2)
        except RuntimeError as e:
            raised = e
        assert raised is not None, \
            "bring-up failure raises RuntimeError (catchable via except Exception)"
        assert "capture card" in str(raised), "error message says what to check"
    finally:
        env_mod.cv2 = real_cv2


# --- second review #7: the evidence frame must BE the model's frame ---
def test_r2_7_step_capture_single_read():
    """The evidence PNG and the model-input JPEG derive from ONE buffer read (same
    instant) -- previously two separate reads: different instants on a changing
    screen, different resolutions, different encodings, while run_log.py's header
    claims step_NN.png is the exact pre-decision frame."""
    import base64
    # Structured but unique frame (gradient + blocks): identity is verifiable and
    # JPEG q90 stays faithful -- pure noise is JPEG's pathological worst case.
    frame = np.zeros((270, 480, 3), np.uint8)
    frame[:, :, 0] = np.linspace(0, 180, 480, dtype=np.uint8)[None, :]
    frame[:, :, 1] = np.linspace(0, 160, 270, dtype=np.uint8)[:, None]
    frame[40:120, 60:200] = (30, 200, 90)
    frame[150:240, 300:450] = (220, 40, 120)
    fake = FakeEnv()
    fake.cam = FakeCam(frame)
    saved_env = al.ENV
    al.ENV = fake
    try:
        png, data_url = al._capture_step_frames()
    finally:
        al.ENV = saved_env
    got = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    assert np.array_equal(got, frame), "evidence PNG is the captured frame, full-res"
    jpeg = base64.b64decode(data_url.split(",", 1)[1])
    got_j = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert got_j.shape[:2] == frame.shape[:2], "same frame, no resize below the input res"
    assert np.abs(got_j.astype(int) - frame.astype(int)).mean() < 5, \
        "model-input JPEG derives from the same instant (lossy, same pixels)"


def test_r2_7_prompt_recorded_in_run_folder():
    """The system prompt travels with the run's meta.json, not only in the global
    holo_requests.jsonl correlated by timestamp."""
    def finish_now(*a, **k):
        return ({"actions": [{"action": "finished", "text": "done"}], "note": None},
                {"content": "{}"}, {})
    saved = _patch_run(finish_now)
    try:
        al.run("x", max_steps=1, confirm_first=0, tag="t_prompt")
    finally:
        _restore_run(saved)
    rec = FakeRecorder.instances[-1]
    assert isinstance(rec.meta.get("system_prompt"), str) \
        and len(rec.meta["system_prompt"]) > 1000, \
        "meta.json carries the full system prompt"


# --- second review #2: exec errors must count against STUCK_LIMIT and reach the model ---
def test_r2_2_exec_errors_count_and_reach_the_model():
    """The exec-error stuck-abort was dead code (stuck reset on every parsed step),
    and the error tool_output was discarded before history threading -- the model
    repeated the rejected action and burned the budget (the winleft class)."""
    class FailR4(FakeR4):
        def move(self, x, y):
            raise ApplianceError("/hid/move not ok: unknown_key:winleft2")

    def click_model(*a, **k):
        return ({"actions": [{"action": "left_click", "coordinate": [10, 10]}], "note": None},
                {"content": "{}"}, {})

    fake = FakeEnv()
    fake.r4 = FailR4()
    saved = (al.ENV, al.call_holo_full, al.RunRecorder)
    al.ENV = fake
    al.call_holo_full = click_model
    al.RunRecorder = FakeRecorder
    FakeRecorder.instances.clear()
    try:
        al.run("click forever", max_steps=6, confirm_first=0, tag="t_execerr")
    finally:
        al.ENV, al.call_holo_full, al.RunRecorder = saved
    rec = FakeRecorder.instances[-1]
    assert rec.finished == (False, "stuck limit hit (exec errors)"), \
        f"consecutive exec errors abort at STUCK_LIMIT, got {rec.finished}"
    assert len(rec.steps) == 3, "aborts after STUCK_LIMIT steps, not the whole budget"
    tool_outputs = [m["content"] for m in al.LAST["history"]
                    if m.get("role") == "user" and isinstance(m.get("content"), str)
                    and m["content"].startswith("<tool_output")]
    assert any("Error:" in t and "unknown_key" in t for t in tool_outputs), \
        "the model is told its action was rejected"


# --- second review #9: screen size is measured after bring-up, not trusted ---
def test_r2_9_screen_size_measured_not_trusted():
    """cap.set(W/H) is a request; if V4L2 falls back to another mode the env must
    adopt the ACTUAL frame size (projection + bridge scale both derive from it)."""
    r4 = FakeR4()
    real_camera, real_make = env_mod.Camera, env_mod.make_hid_client
    env_mod.Camera = lambda *a, **k: FakeCam(np.zeros((720, 1280, 3), np.uint8))
    env_mod.make_hid_client = lambda: r4
    try:
        e = env_mod.PicoEnv(cam_index=0, screen_size=(1920, 1080), show=False)
    finally:
        env_mod.Camera, env_mod.make_hid_client = real_camera, real_make
    assert (e.screen_width, e.screen_height) == (1280, 720), \
        "env adopts the ACTUAL negotiated capture size, not the configured fiction"
    assert ("set_screen", 1280, 720) in r4.calls, \
        "the bridge scale syncs to the measured size"


# --- second review #10: the freshness floor starts AFTER the fire ---
def test_r2_10_freshness_floor_starts_after_the_fire():
    """seq0 was read at _execute entry, before the HID fire -- frames arriving during
    the fire satisfied wait_newer while predating the effect."""
    events = []

    class OrdCam(FakeCam):
        @property
        def seq(self):
            events.append("seq")
            return 1
        def wait_newer(self, seq, timeout_s):
            return FRAME, 2

    class OrdR4(FakeR4):
        def move(self, x, y): events.append("move")
        def click(self): events.append("click")

    fake = FakeEnv()
    fake.cam = OrdCam()
    fake.r4 = OrdR4()
    saved_env = al.ENV
    al.ENV = fake
    try:
        al._execute({"action": "left_click", "coordinate": [1, 1]}, settle_s=0.1)
    finally:
        al.ENV = saved_env
    assert events.index("seq") > events.index("click"), \
        "seq0 is taken AFTER the last HID command returns"


# --- second review #11: unsupported/no-op actions are reported as NOT executed ---
def test_r2_11_unsupported_actions_report_not_executed():
    fake = FakeEnv()
    saved_env, saved_cur = al.ENV, al.CURSOR["pos"]
    al.ENV = fake
    al.CURSOR["pos"] = None
    try:
        info = al._execute({"action": "drag_to", "coordinate": [500, 500]}, settle_s=0.1)
        assert info.get("noop"), "cursorless drag reports a no-op"
        info = al._execute({"action": "scroll", "direction": "left", "scroll_size": 3},
                           settle_s=0.1)
        assert info.get("noop") and "vertical" in info["noop"], \
            "left/right scroll reports a no-op (vertical-only firmware)"
    finally:
        al.ENV = saved_env
        al.CURSOR["pos"] = saved_cur

    def scroll_left_model(*a, **k):
        return ({"actions": [{"action": "scroll", "direction": "left"},
                             {"action": "finished", "text": "done"}], "note": None},
                {"content": "{}"}, {})
    saved = _patch_run(scroll_left_model)
    try:
        al.run("scroll left", max_steps=2, confirm_first=0, tag="t_noop")
    finally:
        _restore_run(saved)
    tool_outputs = [m["content"] for m in al.LAST["history"]
                    if m.get("role") == "user" and isinstance(m.get("content"), str)
                    and m["content"].startswith("<tool_output")]
    assert any("NOT executed" in t for t in tool_outputs), \
        "the model is told the action was not performed"


# --- second review #12: per-run state resets, blind-history guard, repeat clamp ---
def test_r2_12_run_resets_cursor_and_plan():
    """The battery reboots the target between tasks: a stale tracked cursor/plan
    from the previous task is wrong by definition."""
    al.CURSOR["pos"] = (5, 5)
    al.PLAN["goals"] = [{"title": "stale", "status": "running"}]
    def finish_now(*a, **k):
        return ({"actions": [{"action": "finished", "text": "done"}], "note": None},
                {"content": "{}"}, {})
    saved = _patch_run(finish_now)
    try:
        al.run("x", max_steps=1, confirm_first=0, tag="t_reset")
        assert al.CURSOR["pos"] is None, "CURSOR resets at run start"
        assert al.PLAN["goals"] == [], "PLAN resets at run start"
    finally:
        _restore_run(saved)
        al.CURSOR["pos"] = None
        al.PLAN["goals"] = []


def test_r2_12_zero_history_images_refused():
    def finish_now(*a, **k):
        return ({"actions": [{"action": "finished", "text": "done"}], "note": None},
                {"content": "{}"}, {})
    saved = _patch_run(finish_now)
    real = al.MAX_HISTORY_IMAGES
    al.MAX_HISTORY_IMAGES = 0
    try:
        raised = False
        try:
            al.run("x", max_steps=1, confirm_first=0, tag="t_blind")
        except ValueError:
            raised = True
    finally:
        al.MAX_HISTORY_IMAGES = real
        _restore_run(saved)
    assert raised, "HOLO_HISTORY_IMAGES=0 raises instead of silently blinding the model"


def test_r2_12_hotkey_repeat_count_clamped():
    fake = FakeEnv()
    combos = []
    fake.r4.combo = lambda spec: combos.append(spec)
    saved_env = al.ENV
    al.ENV = fake
    try:
        al._execute({"action": "hotkey", "keys": ["ctrl", "tab"], "repeat_count": 999},
                    settle_s=0.1)
    finally:
        al.ENV = saved_env
    assert 0 < len(combos) <= 10, f"repeat_count is clamped, fired {len(combos)}"


if __name__ == "__main__":
    import sys, traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except Exception:
            fails += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print("\n" + ("ALL PASS" if not fails else f"{fails} FAILED"))
    sys.exit(1 if fails else 0)
