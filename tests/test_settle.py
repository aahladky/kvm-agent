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

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)

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

# (a) truly stable sequence -> returns well before max_s
t0 = time.time()
wait_until_stable(scripted([BASE.copy() for _ in range(50)]), max_s=2.0, poll_s=0.005)
check("stable sequence settles fast", time.time() - t0 < 1.0)

# (b) small localized change every poll (a 40x40 block toggling) -> NOT stable,
#     must burn the whole window (the case the whole-frame mean missed).
#     200 frames: at poll_s=0.005 the script must outlast the 0.4s window, else the
#     exhausted script's repeated last frame reads as "stable" (50 frames = 0.25s < 0.4s).
churn = []
for i in range(200):
    f = BASE.copy()
    if i % 2:
        f[100:140, 200:240] = 255
    churn.append(f)
t0 = time.time()
wait_until_stable(scripted(churn), max_s=0.4, poll_s=0.005)
check("localized churn never reads as stable", time.time() - t0 >= 0.35)

# (c) uniform +1 noise everywhere -> below threshold, reads as stable
noise = [(BASE.astype(int) + (i % 2)).clip(0, 255).astype(np.uint8) for i in range(50)]
t0 = time.time()
wait_until_stable(scripted(noise), max_s=2.0, poll_s=0.005)
check("uniform low-level noise reads as stable", time.time() - t0 < 1.0)

# (d) status return (2026-07-21 review P0-5): callers can distinguish "settled" from
#     "still churning at the deadline" from "capture delivered nothing at all" --
#     previously all three returned None, so a dead capture read as instant stability.
s = wait_until_stable(scripted([BASE.copy() for _ in range(50)]), max_s=2.0, poll_s=0.005)
check("settled window reports 'stable'", s == "stable")
s = wait_until_stable(scripted(churn), max_s=0.4, poll_s=0.005)
check("churn window reports 'timeout'", s == "timeout")
s = wait_until_stable(lambda: None, max_s=0.2, poll_s=0.005)
check("all-None window reports 'dead'", s == "dead")

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
