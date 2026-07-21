"""
test_frame_buffer.py — OFFLINE test for the frame-freshness store (finding #6:
no guarantee a post-action frame was captured after the action).

    python tests/test_frame_buffer.py
"""
import sys, os, time, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from kvm_agent.hardware.env import FrameBuffer

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)

fb = FrameBuffer()
check("empty buffer seq 0, frame None", fb.seq == 0 and fb.get() == (None, 0))

f1 = np.zeros((4, 4, 3), np.uint8)
s1 = fb.put(f1)
check("put returns seq 1", s1 == 1)
got, gseq = fb.get()
check("get returns latest + seq", gseq == 1 and got is f1)

f2 = np.ones((4, 4, 3), np.uint8)
fb.put(f2)
check("seq advances monotonically", fb.seq == 2 and fb.get()[0] is f2)

# wait_newer returns promptly once a newer frame lands (producer on another thread)
fb2 = FrameBuffer()
fb2.put(f1)
threading.Timer(0.05, lambda: fb2.put(f2)).start()
t0 = time.time()
frame, seq = fb2.wait_newer(1, timeout_s=2.0)
check("wait_newer returns the newer frame", seq == 2 and frame is f2 and time.time() - t0 < 1.0)

# wait_newer times out loudly when nothing newer arrives
try:
    fb2.wait_newer(99, timeout_s=0.1)
    check("wait_newer times out with TimeoutError", False)
except TimeoutError:
    check("wait_newer times out with TimeoutError", True)

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
