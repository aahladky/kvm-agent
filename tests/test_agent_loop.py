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
        self.screen_width, self.screen_height = 1280, 720
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

    saved = (al.ENV, al.PicoEnv, target_mod.verify_hid, dict(al.SERVING))
    try:
        al.PicoEnv = lambda *a, **k: FakeEnv()
        target_mod.verify_hid = lambda r4, cam, **k: (False, "mouse NOT delivering (test)")
        al.ENV = None
        raised = False
        try:
            # serving_check=False throughout: this test is about the HID gate, and the
            # serving probe would put a real HTTP call in the offline suite.
            al.boot(serving_check=False)
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
        assert al.boot(serving_check=False) is True, \
            "boot() returns True when the gate passes"
        assert gate_calls["n"] == 1, "the gate actually ran"
        al.ENV = None
        al.boot(verify=False, serving_check=False)
        assert gate_calls["n"] == 1, "boot(verify=False) skips the gate"
    finally:
        al.ENV, al.PicoEnv, target_mod.verify_hid = saved[:3]
        al.SERVING.clear()
        al.SERVING.update(saved[3])


def test_boot_serving_check_is_skippable_and_records_when_run():
    """The serving preflight is opt-outable (the offline suite opts out) and, when it
    runs, it WARNS rather than raising -- unlike the HID gate. Clicking into a dead HID
    corrupts silently; every serving problem announces itself at the first model call."""
    import kvm_agent.hardware.target as target_mod

    saved = (al.ENV, al.PicoEnv, target_mod.verify_hid, al.serving_snapshot,
             dict(al.SERVING))
    try:
        al.PicoEnv = lambda *a, **k: FakeEnv()
        target_mod.verify_hid = lambda r4, cam, **k: (True, "hid ok (test)")

        probes = {"n": 0}
        def exploding_probe(*a, **k):
            probes["n"] += 1
            raise AssertionError("serving_check=False must not probe")
        al.serving_snapshot = exploding_probe
        al.ENV = None
        al.boot(verify=False, serving_check=False)
        assert probes["n"] == 0

        # An UNCONFIGURED model warns loudly and still returns -- no raise.
        al.serving_snapshot = lambda *a, **k: {
            "endpoint": "http://x", "model": "holo3.1", "reachable": True,
            "configured": False, "resident": None, "params": {}, "co_resident": [],
            "error": None}
        al.ENV = None
        assert al.boot(verify=False, serving_check=True) is True, \
            "a serving problem must not abort boot"
        assert al.SERVING["checked"] is True and al.SERVING["configured"] is False, \
            "the snapshot is cached for run() to record"
    finally:
        al.ENV, al.PicoEnv, target_mod.verify_hid, al.serving_snapshot = saved[:4]
        al.SERVING.clear()
        al.SERVING.update(saved[4])


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


# =========================================================================
# Pre-fire TOCTOU guard (2026-07-22, SESSION_2026-07-22 finding 2): the screen
# re-flowed during the model's think time and a click correct against the
# decision frame activated the row that slid under it (paint_line s09).
# =========================================================================

# FakeEnv screen is 1280x720; the decision frame is FakeCam's FRAME (270x480 zeros,
# via _capture_step_frames -> cam.read). The guard's pre-fire frame is observe().
# Target coordinate (640, 360) -> tile row 4, col 8 on the 9x16 grid; on the metric's
# 480x270 working size that tile is rows 120:150, cols 240:270.

def _changed_at_target():
    """A frame whose ONLY change from FRAME sits under the target coordinate."""
    arr = np.zeros((270, 480, 3), np.uint8)
    arr[120:150, 240:270] = 255
    ok, buf = cv2.imencode(".png", arr)
    return buf.tobytes()


def _changed_away_from_target():
    """Same-magnitude change, but in the far top-left corner tile (row 0, col 0)."""
    arr = np.zeros((270, 480, 3), np.uint8)
    arr[0:30, 0:30] = 255
    ok, buf = cv2.imencode(".png", arr)
    return buf.tobytes()


class GuardEnv(FakeEnv):
    """observe() pops from a frame queue (each pop = one _frame_png read: the
    per-action pre-fire grab or a post-action diff frame), then serves base."""
    def __init__(self, observe_queue=None):
        super().__init__()
        self.observe_queue = list(observe_queue or [])
    def observe(self):
        if self.observe_queue:
            return {"screenshot": self.observe_queue.pop(0)}
        return {"screenshot": _png(0)}


def _patch_guard_run(env, model_fn):
    saved = (al.ENV, al.call_holo_full, al.RunRecorder)
    al.ENV = env
    al.call_holo_full = model_fn
    al.RunRecorder = FakeRecorder
    FakeRecorder.instances.clear()
    return saved


def _tool_outputs():
    return [m["content"] for m in al.LAST["history"]
            if m.get("role") == "user" and isinstance(m.get("content"), str)
            and m["content"].startswith("<tool_output")]


def test_guard_refuses_and_run_continues():
    """A change under the target between decision and firing: the click must NOT
    fire, the model is told, the step is threaded, and the run continues."""
    responses = [
        ({"actions": [{"action": "left_click", "coordinate": [640, 360]}], "note": None},
         {"content": "{}"}, {}),
        ({"actions": [{"action": "finished", "text": "done"}], "note": None},
         {"content": "{}"}, {}),
    ]
    def model(*a, **k):
        return responses.pop(0)
    fake = GuardEnv(observe_queue=[_changed_at_target()])
    saved = _patch_guard_run(fake, model)
    try:
        result = al.run("click the row", max_steps=4, confirm_first=0, tag="t_guard")
    finally:
        al.ENV, al.call_holo_full, al.RunRecorder = saved
    assert not any(c[0] == "click" for c in fake.r4.calls), "the guarded click never fired"
    assert result["finished"] is True, "the run continues (re-observe) after a refusal"
    outs = _tool_outputs()
    assert any("NOT executed" in t and "changed near the target" in t for t in outs), \
        "the model is told the click was refused and why"
    rec = FakeRecorder.instances[-1]
    assert rec.steps[0][1].get("executed") is False, "a guard-refused step records executed=False"
    logged_step = rec.steps[0][0][3]
    assert any("guard_refusal" in w for w in logged_step.get("warnings", [])), \
        "the refusal reaches the recorder's step warnings"


def test_guard_only_first_action_and_clean_fires():
    """A clean pre-fire frame lets the click fire, and the guard runs exactly once
    per batch (actions 2..N were decided anticipating the batch's own effects)."""
    calls = {"n": 0}
    real_guard = al.tile_region_max_png
    def counting_guard(*a, **k):
        calls["n"] += 1
        return real_guard(*a, **k)
    def model(*a, **k):
        return ({"actions": [{"action": "left_click", "coordinate": [640, 360]},
                             {"action": "left_click", "coordinate": [100, 100]},
                             {"action": "finished", "text": "done"}], "note": None},
                {"content": "{}"}, {})
    fake = GuardEnv()   # observe always serves base: nothing changed
    saved = _patch_guard_run(fake, model)
    al.tile_region_max_png = counting_guard
    try:
        result = al.run("click twice", max_steps=2, confirm_first=0, tag="t_guard_clean")
    finally:
        al.ENV, al.call_holo_full, al.RunRecorder = saved
        al.tile_region_max_png = real_guard
    assert result["finished"] is True
    assert sum(c[0] == "click" for c in fake.r4.calls) == 2, "both clicks fired"
    assert calls["n"] == 1, f"guard checks only the batch's first action, ran {calls['n']}x"


def test_guard_ignores_change_away_from_target():
    """A change far from the target region must NOT refuse the click (the guard is
    regional -- a whole-frame diff would refuse on every clock tick)."""
    def model(*a, **k):
        return ({"actions": [{"action": "left_click", "coordinate": [640, 360]},
                             {"action": "finished", "text": "done"}], "note": None},
                {"content": "{}"}, {})
    fake = GuardEnv(observe_queue=[_changed_away_from_target()])
    saved = _patch_guard_run(fake, model)
    try:
        result = al.run("click", max_steps=2, confirm_first=0, tag="t_guard_away")
    finally:
        al.ENV, al.call_holo_full, al.RunRecorder = saved
    assert any(c[0] == "click" for c in fake.r4.calls), "off-target change doesn't refuse"
    assert result["finished"] is True
    assert not any("NOT executed" in t for t in _tool_outputs())


def test_guard_livelock_aborts_loudly():
    """A permanently animated target region (spinner/clock under the target) must
    abort after GUARD_REFUSE_LIMIT consecutive refusals, not refuse forever --
    and never fire anyway."""
    def model(*a, **k):
        return ({"actions": [{"action": "left_click", "coordinate": [640, 360]}], "note": None},
                {"content": "{}"}, {})
    fake = GuardEnv(observe_queue=[_changed_at_target()] * 10)
    saved = _patch_guard_run(fake, model)
    try:
        result = al.run("click the spinner", max_steps=8, confirm_first=0, tag="t_guard_live")
    finally:
        al.ENV, al.call_holo_full, al.RunRecorder = saved
    rec = FakeRecorder.instances[-1]
    assert result == {"finished": False, "answer_text": ""}
    assert rec.finished is not None and rec.finished[0] is False \
        and "unstable" in rec.finished[1], f"loud unstable-target abort, got {rec.finished}"
    assert len(rec.steps) == al.GUARD_REFUSE_LIMIT, \
        "aborts at the refusal limit, not the whole budget"
    assert not any(c[0] == "click" for c in fake.r4.calls), "never fires anyway"


def test_guard_survives_leading_update_plan():
    """[update_plan, left_click]: the plan update must not burn the guard -- the
    click is still the batch's first SCREEN-AFFECTING action."""
    responses = [
        ({"actions": [{"action": "update_plan",
                       "goals": [{"title": "g", "status": "running"}]},
                      {"action": "left_click", "coordinate": [640, 360]}], "note": None},
         {"content": "{}"}, {}),
        ({"actions": [{"action": "finished", "text": "done"}], "note": None},
         {"content": "{}"}, {}),
    ]
    def model(*a, **k):
        return responses.pop(0)
    # observe #1 = the update_plan action's pre-fire grab (base), observe #2 = the
    # click's pre-fire grab (changed at target).
    fake = GuardEnv(observe_queue=[_png(0), _changed_at_target()])
    saved = _patch_guard_run(fake, model)
    try:
        result = al.run("plan then click", max_steps=4, confirm_first=0, tag="t_guard_plan")
    finally:
        al.ENV, al.call_holo_full, al.RunRecorder = saved
    assert not any(c[0] == "click" for c in fake.r4.calls), \
        "the click after update_plan is still guarded"
    outs = _tool_outputs()
    assert any("Plan updated." in t for t in outs), "the plan update still ACKs"
    assert any("NOT executed" in t and "changed near the target" in t for t in outs)
    assert result["finished"] is True, "the run continues after the refusal"


# --- roadmap Phase 2 slice D-b: shadow verification wiring ---
# docs/PLAN_2026-07-22_phase2_subgoal_verification.md. verify_mode="off" (the default)
# must be provably identical to pre-D-b run(): same control flow, same EXACT return
# dict, verifier never constructed or touched. "shadow" must record a verdict without
# changing anything about how the run proceeds or concludes.

def _finish_now(*a, **k):
    return ({"actions": [{"action": "finished", "text": "done"}], "note": None},
            {"content": "{}"}, {})


class _StubVerifier:
    """Records every call so a test can assert what question/claim/image it received,
    without needing a real kvm_agent.models.holo.HoloVerifier or a network call."""
    def __init__(self, satisfied=True, evidence="stub evidence"):
        self.satisfied, self.evidence = satisfied, evidence
        self.calls = []

    def check(self, data_url, w, h, question, claim=""):
        self.calls.append({"data_url": data_url, "w": w, "h": h,
                           "question": question, "claim": claim})
        from kvm_agent.models.base import Verdict
        return Verdict(satisfied=self.satisfied, evidence=self.evidence,
                       raw={}, usage={"prompt_tokens": 7}, wall_time_s=0.05)


class _ExplodingVerifier:
    """.check() must never be reached in verify_mode='off' -- any call is a bug."""
    def check(self, *a, **k):
        raise AssertionError("verifier.check() must not be called in verify_mode='off'")


def test_d_b_off_is_byte_identical_to_pre_d_b_run():
    """The default path: no verify_mode/verifier args at all, same as every OTHER test
    in this file that predates D-b. Must still return EXACTLY the two original keys."""
    saved = _patch_run(_finish_now)
    try:
        result = al.run("x", max_steps=1, confirm_first=0, tag="t_off_default")
    finally:
        _restore_run(saved)
    assert result == {"finished": True, "answer_text": "done"}, \
        "no 'verified_finish' key may appear when verify_mode is left at its default"


def test_d_b_off_ignores_a_supplied_verifier_entirely():
    """Even if a caller passes a verifier, verify_mode='off' must never construct or
    call it, and the return shape stays exactly the pre-D-b two keys."""
    saved = _patch_run(_finish_now)
    exploding = _ExplodingVerifier()
    try:
        result = al.run("x", max_steps=1, confirm_first=0, tag="t_off_explicit",
                        verify_mode="off", verifier=exploding)
    finally:
        _restore_run(saved)
    assert result == {"finished": True, "answer_text": "done"}


def test_d_b_unknown_verify_mode_rejected_loudly():
    for bad in ("on", "SHADOW", "", None):
        try:
            al.run("x", max_steps=1, verify_mode=bad)
        except ValueError:
            continue
        raise AssertionError(f"verify_mode={bad!r} must be rejected")


def test_d_b_gate_mode_is_not_implemented_yet():
    """Slice D-c's mode. Must be rejected LOUDLY (NotImplementedError), never silently
    behaving like 'shadow' or 'off' -- a silent no-op here would hide that the gate
    everyone assumes is active isn't."""
    for verifier in (None, _StubVerifier()):
        try:
            al.run("x", max_steps=1, verify_mode="gate", verifier=verifier)
        except NotImplementedError:
            continue
        raise AssertionError("verify_mode='gate' must raise NotImplementedError")


def test_d_b_shadow_requires_a_verifier():
    """Checked EAGERLY (before any steps run), not only when a `finished` claim
    happens to occur -- a run that never finishes would otherwise let a missing
    verifier hide for an entire battery task."""
    try:
        al.run("x", max_steps=1, verify_mode="shadow", verifier=None)
    except ValueError:
        return
    raise AssertionError("verify_mode='shadow' with verifier=None must raise")


def test_d_b_shadow_records_a_verdict_without_changing_control_flow():
    stub = _StubVerifier(satisfied=True, evidence="calculator shows 56")
    saved = _patch_run(_finish_now)
    try:
        result = al.run("compute 7 times 8", max_steps=1, confirm_first=0,
                        tag="t_shadow_pass", verify_mode="shadow", verifier=stub)
    finally:
        _restore_run(saved)
    assert result["finished"] is True and result["answer_text"] == "done", \
        "shadow mode must not alter the finished/answer_text the model itself reported"
    assert result["verified_finish"] == {
        "satisfied": True, "evidence": "calculator shows 56",
        "wall_time_s": 0.05, "usage": {"prompt_tokens": 7}}

    assert len(stub.calls) == 1, "the finished action is verified exactly once"
    call = stub.calls[0]
    assert call["question"] == "compute 7 times 8", \
        "the verifier is asked about the TASK instruction, not the empty step_instruction"
    assert call["claim"] == "done", "the model's own answer text is passed as the claim"
    assert call["data_url"].startswith("data:image/jpeg;base64,"), \
        "the verifier sees a JPEG data URL, the same encoding the actor's own input uses"

    rec = FakeRecorder.instances[-1]
    logged_kwargs = rec.steps[-1][1]
    assert logged_kwargs["verification"] == result["verified_finish"], \
        "the same verdict reaches the recorder's step record, not a re-derived copy"
    assert rec.finished == (True, "done"), "recorder.finish() is unaffected by shadow mode"


def test_d_b_shadow_records_an_unsatisfied_verdict_too():
    """Recording a False verdict is just as important as recording a True one -- this
    is the live false-refusal/false-confirmation signal D-b exists to gather."""
    stub = _StubVerifier(satisfied=False, evidence="calculator still shows 0")
    saved = _patch_run(_finish_now)
    try:
        result = al.run("compute 7 times 8", max_steps=1, confirm_first=0,
                        tag="t_shadow_fail", verify_mode="shadow", verifier=stub)
    finally:
        _restore_run(saved)
    assert result["finished"] is True, \
        "shadow mode NEVER gates -- an unsatisfied verdict still lets the run finish"
    assert result["verified_finish"]["satisfied"] is False
    assert "still shows 0" in result["verified_finish"]["evidence"]


def test_d_b_a_raising_verifier_becomes_satisfied_none_not_a_dead_run():
    """Defense in depth, same reasoning as the session.decide() guard above (P0-2): an
    unexpected raise from ANY verifier must not propagate past recorder.finish(), or
    one bad verifier call kills every remaining battery task."""
    class _RaisingVerifier:
        def check(self, *a, **k):
            raise RuntimeError("endpoint exploded")
    saved = _patch_run(_finish_now)
    try:
        result = al.run("x", max_steps=1, confirm_first=0, tag="t_shadow_raise",
                        verify_mode="shadow", verifier=_RaisingVerifier())
    finally:
        _restore_run(saved)
    assert result["finished"] is True, "a raising verifier must not kill the run"
    vf = result["verified_finish"]
    assert vf["satisfied"] is None, "a raise is a non-answer, never coerced to a verdict"
    assert "endpoint exploded" in vf["evidence"]


def test_d_b_verified_finish_present_and_none_on_every_abort_path():
    """When verify_mode != 'off', EVERY return path gains the key (never absent),
    always None where no claim was ever made -- a consumer should never need a
    conditional .get() depending on WHY the run ended."""
    def never_finishes(*a, **k):
        return ({"actions": [], "note": None, "error": "bad json"}, {}, {})
    stub = _StubVerifier()
    saved = _patch_run(never_finishes)
    try:
        result = al.run("x", max_steps=al.STUCK_LIMIT, confirm_first=0,
                        tag="t_shadow_abort", verify_mode="shadow", verifier=stub)
    finally:
        _restore_run(saved)
    assert result["finished"] is False
    assert "verified_finish" in result and result["verified_finish"] is None
    assert stub.calls == [], "no finished claim was ever made -- the verifier is never called"


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
