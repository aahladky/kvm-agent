"""
test_frame_diff.py — OFFLINE test for the tile-max frame-diff metric (flaw #4 fix).

Confirms the new _frame_diff_score catches a small LOCALIZED change (the case the old
whole-frame-mean metric missed, e.g. a calculator digit) while ignoring uniform low-level
noise -- the two behaviours that motivated the rewrite.

    python tests/test_frame_diff.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import cv2
from agent_loop_holo import _frame_diff_score, _frame_changed, FRAME_CHANGE_THRESHOLD


def png(arr):
    ok, buf = cv2.imencode(".png", arr)
    return buf.tobytes()


base = np.full((1080, 1920), 128, np.uint8)
same = png(base)


def test_identical_frames_no_change():
    assert _frame_diff_score(same, same) == 0.0, "identical frames -> score 0.0"
    assert _frame_changed(same, same) is False, "identical frames -> not changed"


# small localized change: a 40x40 bright block (a digit/char-sized region) -- the exact class
# the old whole-frame mean averaged into ~nothing.
def test_small_localized_change_detected():
    b2 = base.copy()
    b2[500:540, 900:940] = 255
    loc = png(b2)
    assert _frame_diff_score(same, loc) > FRAME_CHANGE_THRESHOLD, \
        "small localized change scores above threshold"
    assert _frame_changed(same, loc) is True, "small localized change -> changed True"


# uniform low-level shift everywhere (noise-like) stays below threshold -- must NOT read as a change.
def test_uniform_noise_below_threshold():
    b3 = (base.astype(int) + 1).clip(0, 255).astype(np.uint8)
    assert _frame_diff_score(same, png(b3)) < FRAME_CHANGE_THRESHOLD, \
        "uniform +1 (noise-like) below threshold"


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
