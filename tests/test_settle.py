"""
test_settle.py — OFFLINE test: wait_until_stable uses the tile-max metric, so a
small LOCALIZED change (calc-digit class, flaw #4) counts as "still changing" while
uniform low-level noise counts as stable.

    python tests/test_settle.py
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from kvm_agent.hardware.env import wait_until_stable

def scripted(frames):
    it = iter(frames)
    last = [frames[-1]]
    def read():
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]
    return read

BASE = np.full((270, 480, 3), 128, np.uint8)


def _churn_frames():
    # small localized change every poll (a 40x40 block toggling) -> NOT stable,
    # must burn the whole window (the case the whole-frame mean missed).
    # 200 frames: at poll_s=0.005 the script must outlast the 0.4s window, else the
    # exhausted script's repeated last frame reads as "stable" (50 frames = 0.25s < 0.4s).
    churn = []
    for i in range(200):
        f = BASE.copy()
        if i % 2:
            f[100:140, 200:240] = 255
        churn.append(f)
    return churn


# (a) truly stable sequence -> returns well before max_s
def test_stable_sequence_settles_fast():
    t0 = time.time()
    wait_until_stable(scripted([BASE.copy() for _ in range(50)]), max_s=2.0, poll_s=0.005)
    assert time.time() - t0 < 1.0, "stable sequence settles fast"


# (b) small localized change every poll -> never reads as stable
def test_localized_churn_never_stable():
    t0 = time.time()
    wait_until_stable(scripted(_churn_frames()), max_s=0.4, poll_s=0.005)
    assert time.time() - t0 >= 0.35, "localized churn never reads as stable"


# (c) uniform +1 noise everywhere -> below threshold, reads as stable
def test_uniform_noise_reads_stable():
    noise = [(BASE.astype(int) + (i % 2)).clip(0, 255).astype(np.uint8) for i in range(50)]
    t0 = time.time()
    wait_until_stable(scripted(noise), max_s=2.0, poll_s=0.005)
    assert time.time() - t0 < 1.0, "uniform low-level noise reads as stable"


# (d) status return (2026-07-21 review P0-5): callers can distinguish "settled" from
#     "still churning at the deadline" from "capture delivered nothing at all" --
#     previously all three returned None, so a dead capture read as instant stability.
def test_status_return_values():
    s = wait_until_stable(scripted([BASE.copy() for _ in range(50)]), max_s=2.0, poll_s=0.005)
    assert s == "stable", "settled window reports 'stable'"
    s = wait_until_stable(scripted(_churn_frames()), max_s=0.4, poll_s=0.005)
    assert s == "timeout", "churn window reports 'timeout'"
    s = wait_until_stable(lambda: None, max_s=0.2, poll_s=0.005)
    assert s == "dead", "all-None window reports 'dead'"


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
