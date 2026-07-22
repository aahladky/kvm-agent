"""
test_model_seam.py — OFFLINE tests for the model-neutral seam (roadmap Phase 1,
docs/ROADMAP.md Part 3 Slice C): kvm_agent.models.base.{StepDecision, ModelSession}
and kvm_agent.models.holo.HoloSession.

Covers:
    - HoloSession.decide()/commit()/tool_name() in isolation (no agent_loop_holo).
    - HoloSession satisfies the ModelSession Protocol (structural check).
    - Golden-transcript equivalence: agent_loop_holo.run()'s history threading through
      HoloSession is byte-identical (mod image payload length) to a fixture captured
      from the PRE-refactor code path (tests/_fixtures/golden_transcript_history.json,
      generation script documented in its own header comment below) for the same
      scripted multi-step, multi-action-batch scenario. Proves the refactor is
      "pure" per the plan's own verification method.

    python -m pytest tests/test_model_seam.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

import agent_loop_holo as al
from kvm_agent.models.base import ModelSession, StepDecision
from kvm_agent.models.holo import ACTION_TO_TOOL_NAME, HoloSession

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fixtures")


# --- HoloSession in isolation -------------------------------------------------

def test_holo_session_satisfies_model_session_protocol():
    assert isinstance(HoloSession(), ModelSession), \
        "HoloSession must structurally satisfy the ModelSession Protocol"


def test_tool_name_maps_every_normalized_kind():
    for kind, native in ACTION_TO_TOOL_NAME.items():
        assert HoloSession().tool_name(kind) == native
    assert HoloSession().tool_name("something_unmapped") == "something_unmapped", \
        "an unmapped kind falls back to its own name, not a crash"


def test_decide_calls_injected_call_fn_with_session_history_and_returns_decision():
    seen = {}
    def fake_call_fn(instruction, data_url, w, h, target=None, history=None,
                      max_history_images=None):
        seen.update(instruction=instruction, data_url=data_url, w=w, h=h,
                    target=target, history=history, max_history_images=max_history_images)
        return ({"actions": [{"action": "left_click", "coordinate": [1, 2]}],
                  "note": "n", "thought": "t"},
                {"content": "raw"}, {"prompt_tokens": 1})

    session = HoloSession(target="local", max_history_images=2, call_fn=fake_call_fn)
    decision = session.decide("data:url", 100, 200, "do it")

    assert seen == {"instruction": "do it", "data_url": "data:url", "w": 100, "h": 200,
                    "target": "local", "history": session.history, "max_history_images": 2}, \
        "decide() must pass session.history (not a copy) as the request's context"
    assert isinstance(decision, StepDecision)
    assert decision.actions == [{"action": "left_click", "coordinate": [1, 2]}]
    assert decision.note == "n" and decision.thought == "t" and decision.error is None
    assert decision.message == {"content": "raw"} and decision.usage == {"prompt_tokens": 1}
    assert session.history == [], "decide() must NOT mutate history -- only commit() does"


def test_commit_threads_observation_assistant_and_results_then_trims():
    session = HoloSession(max_history_images=1)
    decision = StepDecision(
        step={"actions": [{"action": "left_click", "coordinate": [1, 2]}]},
        message={"content": "assistant-turn"}, usage={},
        data_url="data:image/jpeg;base64,AAAA", instruction="go")
    session.commit(decision, [("click_desktop", "Executed. Screen changed.")])

    assert len(session.history) == 3
    assert session.history[0]["role"] == "user"
    obs_texts = [c for c in session.history[0]["content"] if c["type"] == "text"]
    assert obs_texts[0]["text"] == "<observation>\ngo\n"
    assert session.history[1] == {"role": "assistant", "content": "assistant-turn"}
    assert session.history[2] == {
        "role": "user",
        "content": '<tool_output tool="click_desktop">\nExecuted. Screen changed.\n</tool_output>',
    }

    # A second commit with its own image must trim the FIRST commit's image down to
    # the evicted marker (max_history_images=1) -- the cumulative trim behavior the
    # golden-transcript test also exercises end-to-end.
    decision2 = StepDecision(
        step={"actions": []}, message={"content": "second"}, usage={},
        data_url="data:image/jpeg;base64,BBBB", instruction="")
    session.commit(decision2, [])
    first_obs_images = [c for c in session.history[0]["content"] if c["type"] == "image_url"]
    assert first_obs_images == [], "the first observation's image is now evicted"
    second_obs_images = [c for c in session.history[3]["content"] if c["type"] == "image_url"]
    assert second_obs_images, "the newest observation's image survives the trim"


def test_commit_not_called_leaves_history_untouched():
    """Mirrors run()'s contract: a parse-failed step's `continue` happens BEFORE
    commit() -- so a caller that never calls commit() must see no history growth."""
    session = HoloSession()
    assert session.history == []


# --- Golden-transcript equivalence (the plan's own verification method) ------
#
# Fixture generation (run ONCE against the pre-refactor code, output committed):
# a scripted 6-step / 7-tool-call scenario (click, type, update_plan+hotkey batch,
# scroll, drag_to, finished) driving agent_loop_holo.run() with a FakeEnv/FakeR4/
# FakeCam/FakeRecorder identical to test_agent_loop.py's, then dumping
# al.LAST["history"] with image_url payloads replaced by a length marker (keeps the
# fixture small and reviewable while still proving image chunks appear/evict in the
# same positions). The one-off generation script ran from scratch/ against the
# pre-refactor agent_loop_holo.py (not shipped -- it imports the pre-refactor module
# shape and would bit-rot as a live file); the scenario is reproduced verbatim below.

def _png(v):
    arr = np.full((270, 480, 3), v, np.uint8)
    ok, buf = cv2.imencode(".png", arr)
    return buf.tobytes()


FRAME = np.zeros((270, 480, 3), np.uint8)


class _FakeCam:
    def __init__(self, frame=None):
        self._frame = frame if frame is not None else FRAME
        self._seq = 0
    @property
    def seq(self):
        self._seq += 1
        return self._seq
    def read(self): return self._frame
    def wait_newer(self, seq, timeout_s): return self._frame, seq + 1
    def model_input_jpeg(self):
        ok, buf = cv2.imencode(".jpg", self._frame)
        return buf.tobytes()


class _FakeR4:
    def __init__(self):
        self.calls = []
    def move(self, x, y): self.calls.append(("move", x, y))
    def click(self): self.calls.append(("click",))
    def down(self): self.calls.append(("down",))
    def up(self): self.calls.append(("up",))
    def combo(self, spec): self.calls.append(("combo", spec))
    def scroll(self, ticks): self.calls.append(("scroll", ticks))
    def type(self, text): self.calls.append(("type", text))
    def key(self, k): self.calls.append(("key", k))
    def clear_hid(self): self.calls.append(("clear_hid",))
    def set_screen(self, w, h): self.calls.append(("set_screen", w, h))


class _FakeEnv:
    def __init__(self):
        self.cam = _FakeCam()
        self.r4 = _FakeR4()
        self.screen_width, self.screen_height = 1280, 720
    def observe(self):
        return {"screenshot": _png(0)}
    def close(self):
        pass


class _FakeRecorder:
    instances = []
    def __init__(self, tag, goal, target=None, meta=None):
        self.tag = tag
        self.meta = meta or {}
        self.steps = []
        self.finished = None
        _FakeRecorder.instances.append(self)
    def log_step(self, *a, **k):
        self.steps.append((a, k))
    def finish(self, success, note=""):
        self.finished = (success, note)
        return {}


_RESPONSES = [
    ({"actions": [{"action": "left_click", "coordinate": [100, 100], "element": "Start"}],
      "note": "n0", "thought": "t0"},
     {"content": "assistant-json-step0"}, {"prompt_tokens": 10, "completion_tokens": 5}),
    ({"actions": [{"action": "type", "text": "hello", "press_enter": False}],
      "note": None, "thought": "t1"},
     {"content": "assistant-json-step1"}, {"prompt_tokens": 11, "completion_tokens": 6}),
    ({"actions": [{"action": "update_plan", "goals": [{"title": "g1", "status": "running"}]},
                  {"action": "hotkey", "keys": ["ctrl", "s"], "repeat_count": 1}],
      "note": None, "thought": "t2"},
     {"content": "assistant-json-step2"}, {"prompt_tokens": 12, "completion_tokens": 7}),
    ({"actions": [{"action": "scroll", "direction": "down", "scroll_size": 3,
                   "coordinate": [200, 200]}],
      "note": None, "thought": "t3"},
     {"content": "assistant-json-step3"}, {"prompt_tokens": 13, "completion_tokens": 8}),
    ({"actions": [{"action": "drag_to", "coordinate": [300, 300], "element": "handle"}],
      "note": None, "thought": "t4"},
     {"content": "assistant-json-step4"}, {"prompt_tokens": 14, "completion_tokens": 9}),
    ({"actions": [{"action": "finished", "text": "done"}], "note": None, "thought": "t5"},
     {"content": "assistant-json-step5"}, {"prompt_tokens": 15, "completion_tokens": 10}),
]


def _normalize_history(history):
    out = []
    for m in history:
        content = m.get("content")
        if isinstance(content, list):
            new_content = []
            for c in content:
                if c.get("type") == "image_url":
                    new_content.append({"type": "image_url",
                                        "image_url_len": len(c["image_url"]["url"])})
                else:
                    new_content.append(c)
            out.append({**m, "content": new_content})
        else:
            out.append(m)
    return out


def test_golden_transcript_matches_pre_refactor_fixture():
    responses = list(_RESPONSES)
    def model_fn(*a, **k):
        return responses.pop(0)

    saved = (al.ENV, al.call_holo_full, al.RunRecorder)
    al.ENV = _FakeEnv()
    al.call_holo_full = model_fn
    al.RunRecorder = _FakeRecorder
    _FakeRecorder.instances.clear()
    try:
        result = al.run("do the scripted scenario", max_steps=8, confirm_first=0,
                        tag="t_golden", no_progress_abort=False)
    finally:
        al.ENV, al.call_holo_full, al.RunRecorder = saved

    assert result == {"finished": True, "answer_text": "done"}
    got = _normalize_history(al.LAST["history"])

    with open(os.path.join(FIXTURES_DIR, "golden_transcript_history.json")) as f:
        expected = json.load(f)

    assert got == expected, \
        "post-refactor history threading (via HoloSession) must be byte-identical " \
        "to the pre-refactor fixture for the same scripted scenario"


# --- Phase-1 gate: a second ModelSession drives run() untouched -------------

class _StubSession:
    """A ModelSession that ISN'T Holo -- no native tool-name vocabulary, no
    structured-output schema, no call_holo_full. Proves the roadmap Phase-1 gate:
    "you could stub a second propose/ground/verify implementation without touching
    the loop" -- run() below never imports or references this class; it's handed in
    via the `session` param."""

    def __init__(self):
        self.history = []
        self.decide_calls = 0

    def reset(self):
        self.history = []

    def decide(self, data_url, w, h, instruction):
        self.decide_calls += 1
        step = {"actions": [{"action": "finished", "text": "stub done"}], "note": None}
        return StepDecision(step=step, message={"content": "stub"}, usage={},
                            data_url=data_url, instruction=instruction)

    def tool_name(self, action_kind):
        return f"stub:{action_kind}"

    def commit(self, decision, results):
        self.history.append((decision.instruction, results))


def test_run_accepts_a_non_holo_model_session():
    def exploding_call_fn(*a, **k):
        raise AssertionError("run() must not touch call_holo_full when a session is injected")

    stub = _StubSession()
    saved = (al.ENV, al.call_holo_full, al.RunRecorder)
    al.ENV = _FakeEnv()
    al.call_holo_full = exploding_call_fn
    al.RunRecorder = _FakeRecorder
    _FakeRecorder.instances.clear()
    try:
        result = al.run("stub task", max_steps=2, confirm_first=0, tag="t_stub",
                        session=stub)
    finally:
        al.ENV, al.call_holo_full, al.RunRecorder = saved

    assert result == {"finished": True, "answer_text": "stub done"}
    assert stub.decide_calls == 1
    assert len(stub.history) == 1
    instruction, results = stub.history[0]
    assert instruction == "stub task"
    assert len(results) == 1 and results[0][0] == "stub:finished", \
        "the harness asked the STUB session for its own tool-name vocabulary, " \
        "never Holo's native names"
