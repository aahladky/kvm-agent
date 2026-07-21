"""
test_settle.py — OFFLINE test: wait_until_stable uses the tile-max metric, so a
small LOCALIZED change (calc-digit class, flaw #4) counts as "still changing" while
uniform low-level noise counts as stable. Also pins the status return (review
2026-07-21 P0-5: settled / timeout / dead-capture all used to return None).

    python tests/test_settle.py   (or pytest tests/test_settle.py)
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from kvm_agent.hardware.env import wait_until_stable

BASE = np.full((270, 480, 3), 128, np.uint8)


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


def test_stable_sequence_settles_fast():
    t0 = time.time()
    status = wait_until_stable(scripted([BASE.copy() for _ in range(50)]), max_s=2.0, poll_s=0.005)
    assert time.time() - t0 < 1.0
    assert status == "settled"


def test_localized_churn_never_stable():
    # small localized change every poll (a 40x40 block toggling) -> NOT stable, must
    # burn the whole window (the case the whole-frame mean missed). 200 frames: at
    # poll_s=0.005 the script must outlast the 0.4s window, else the exhausted
    # script's repeated last frame reads as "stable".
    churn = []
    for i in range(200):
        f = BASE.copy()
        if i % 2:
            f[100:140, 200:240] = 255
        churn.append(f)
    t0 = time.time()
    status = wait_until_stable(scripted(churn), max_s=0.4, poll_s=0.005)
    assert time.time() - t0 >= 0.35
    assert status == "timeout"


def test_uniform_noise_reads_stable():
    noise = [(BASE.astype(int) + (i % 2)).clip(0, 255).astype(np.uint8) for i in range(50)]
    t0 = time.time()
    status = wait_until_stable(scripted(noise), max_s=2.0, poll_s=0.005)
    assert time.time() - t0 < 1.0
    assert status == "settled"


def test_dead_capture_distinguishable():
    # review P0-5: a dead capture must not be indistinguishable from a settled screen
    assert wait_until_stable(lambda: None, max_s=0.1, poll_s=0.005) == "no_frames"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
