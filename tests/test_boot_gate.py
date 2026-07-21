"""
test_boot_gate.py — OFFLINE test: boot() runs the camera-verified HID gate by default
and fails LOUD + re-runnable (review 2026-07-21 P0-4: the gate only protected the
battery; REPL sessions drove an unverified channel -- the exact "probe flags LIE"
case verify_hid exists for).

    python tests/test_boot_gate.py   (or pytest tests/test_boot_gate.py)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_loop_holo


class FakeR4:
    def set_screen(self, w, h):
        pass


class FakeEnv:
    def __init__(self, cam_index=0, screen_size=(1920, 1080), show=False):
        self.screen_width, self.screen_height = screen_size
        self.r4 = FakeR4()
        self.cam = object()
    def close(self):
        pass


def _with_gate(gate, verify=True):
    """Run boot() with PicoEnv+verify_hid faked; returns (return/exception, gate_calls)."""
    gate_calls = []
    def spy(r4, cam, screen=(1920, 1080), attempts=2, **kw):
        gate_calls.append({"screen": screen, "attempts": attempts})
        return gate(r4, cam)
    saved = (agent_loop_holo.PicoEnv, agent_loop_holo.ENV, agent_loop_holo.verify_hid)
    agent_loop_holo.PicoEnv = FakeEnv
    agent_loop_holo.ENV = None
    agent_loop_holo.verify_hid = spy
    try:
        try:
            out = agent_loop_holo.boot(verify=verify)
        except RuntimeError as e:
            out = e
        return out, gate_calls, agent_loop_holo.ENV
    finally:
        agent_loop_holo.PicoEnv, agent_loop_holo.ENV, agent_loop_holo.verify_hid = saved


def test_gate_failure_raises_loud_and_rerunnable():
    out, calls, env = _with_gate(lambda r4, cam: (False, "keyboard NOT delivering (win+r diff 0.0 <= 20.0)"))
    assert isinstance(out, RuntimeError), "gate failure must raise"
    assert "keyboard NOT delivering" in str(out), "the raise carries the gate's diagnosis"
    assert env is None, "failed boot leaves ENV reset (re-runnable)"
    assert calls and calls[0]["attempts"] == 1, "boot gate is single-attempt (non-interactive)"


def test_gate_pass_boots():
    out, calls, env = _with_gate(lambda r4, cam: (True, "hid ok"))
    assert out is True
    assert calls[-1]["screen"] == (1920, 1080), "gate runs against the env's screen space"


def test_verify_false_skips_gate():
    out, calls, env = _with_gate(lambda r4, cam: (False, "would fail"), verify=False)
    assert out is True and calls == [], "boot(verify=False) must skip the gate"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
