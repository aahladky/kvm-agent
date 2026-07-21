"""
test_frame_diff.py — OFFLINE test for the tile-max frame-diff metric (flaw #4 fix).

Confirms the metric catches a small LOCALIZED change (the case the old
whole-frame-mean metric missed, e.g. a calculator digit) while ignoring uniform
low-level noise -- the two behaviours that motivated the rewrite. Canonical home is
kvm_agent.hardware.env since 2026-07-21 (review P3); agent_loop_holo's _frame_*
names must stay importable aliases.

    python tests/test_frame_diff.py   (or pytest tests/test_frame_diff.py)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import cv2

from kvm_agent.config import CFG
from kvm_agent.hardware.env import frame_diff_detail, frame_diff_score


def png(arr):
    ok, buf = cv2.imencode(".png", arr)
    return buf.tobytes()


BASE = np.full((1080, 1920), 128, np.uint8)
SAME = png(BASE)


def test_identical_frames_score_zero():
    assert frame_diff_score(SAME, SAME) == 0.0


def test_small_localized_change_detected():
    # a 40x40 bright block (digit/char-sized) -- the class the whole-frame mean missed
    b2 = BASE.copy()
    b2[500:540, 900:940] = 255
    loc = png(b2)
    score, region = frame_diff_detail(SAME, loc)
    assert score > CFG.frame_change_threshold
    assert region == "center", f"the changed tile is mid-screen, got {region!r}"


def test_uniform_noise_below_threshold():
    b3 = (BASE.astype(int) + 1).clip(0, 255).astype(np.uint8)
    assert frame_diff_score(SAME, png(b3)) < CFG.frame_change_threshold


def test_agent_loop_aliases_still_work():
    # callers/tests import these via the app script; they must track env's metric
    from agent_loop_holo import _frame_diff_score, _frame_changed, FRAME_CHANGE_THRESHOLD
    assert FRAME_CHANGE_THRESHOLD == CFG.frame_change_threshold
    assert _frame_diff_score(SAME, SAME) == 0.0
    assert _frame_changed(SAME, SAME) is False


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
