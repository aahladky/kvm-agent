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

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)


def png(arr):
    ok, buf = cv2.imencode(".png", arr)
    return buf.tobytes()


base = np.full((1080, 1920), 128, np.uint8)
same = png(base)

check("identical frames -> score 0.0", _frame_diff_score(same, same) == 0.0)
check("identical frames -> not changed", _frame_changed(same, same) is False)

# small localized change: a 40x40 bright block (a digit/char-sized region) -- the exact class
# the old whole-frame mean averaged into ~nothing.
b2 = base.copy()
b2[500:540, 900:940] = 255
loc = png(b2)
check("small localized change scores above threshold",
      _frame_diff_score(same, loc) > FRAME_CHANGE_THRESHOLD)
check("small localized change -> changed True", _frame_changed(same, loc) is True)

# uniform low-level shift everywhere (noise-like) stays below threshold -- must NOT read as a change.
b3 = (base.astype(int) + 1).clip(0, 255).astype(np.uint8)
check("uniform +1 (noise-like) below threshold",
      _frame_diff_score(same, png(b3)) < FRAME_CHANGE_THRESHOLD)

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
