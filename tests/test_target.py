"""
test_target.py — OFFLINE test for the manual power/reset seam and the
camera-verified HID gate (fake r4 + fake cam round-trips).

    python tests/test_target.py   (or pytest tests/test_target.py)
"""
import sys, os, builtins
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import cv2

from kvm_agent.hardware import target


def test_reboot_blocks_on_operator():
    calls = []
    real_input = builtins.input
    builtins.input = lambda prompt="": calls.append(prompt) or ""
    try:
        target.reboot()
    finally:
        builtins.input = real_input
    assert len(calls) == 1, "reboot() blocks on operator confirmation exactly once"
    assert "power-cycle" in calls[0].lower(), "prompt tells the operator what to do"
    assert target.is_up() is True, "is_up() True after confirmation (v1 contract)"


# --- verify_hid gate ---

def _png(v):
    arr = np.full((270, 480, 3), v, np.uint8)
    ok, buf = cv2.imencode(".png", arr)
    return buf.tobytes()


SAME, CHANGED = _png(128), _png(255)   # tile-max diff 127 >> thresh=20


class FakeR4:
    def __init__(self):
        self.calls = []
    def combo(self, s): self.calls.append(("combo", s))
    def key(self, k): self.calls.append(("key", k))
    def move(self, x, y): self.calls.append(("move", x, y))
    def click(self): self.calls.append(("click",))


class FakeCam:
    """png_bytes() pops a scripted sequence; read() returns a constant frame so
    wait_until_stable sees instant stability."""
    def __init__(self, frames):
        self.frames = list(frames)
        self._still = np.zeros((270, 480, 3), np.uint8)
    def png_bytes(self, full_res=False):
        return self.frames.pop(0) if self.frames else SAME
    def read(self):
        return self._still


def test_gate_passes_when_both_roundtrips_change():
    r4 = FakeR4()
    ok, detail = target.verify_hid(r4, FakeCam([SAME, CHANGED, SAME, CHANGED]))
    assert ok is True
    assert ("combo", "win+r") in r4.calls and ("key", "esc") in r4.calls, "keyboard round-trip ran"
    assert ("move", 20, 1055) in r4.calls and ("click",) in r4.calls, "mouse round-trip ran"


def test_gate_fails_closed_when_nothing_changes():
    ok, detail = target.verify_hid(FakeR4(), FakeCam([SAME, SAME, SAME, SAME]))
    assert ok is False
    assert "keyboard" in detail, "names the dead collection (keyboard first)"


def test_gate_catches_half_dead_mouse():
    ok, detail = target.verify_hid(FakeR4(), FakeCam([SAME, CHANGED, SAME, SAME]))
    assert ok is False and "mouse" in detail


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
