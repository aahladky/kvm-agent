"""
test_frame_buffer.py — OFFLINE test for the frame-freshness store (finding #6:
no guarantee a post-action frame was captured after the action).

    python tests/test_frame_buffer.py   (or pytest tests/test_frame_buffer.py)
"""
import sys, os, time, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from kvm_agent.hardware.env import FrameBuffer

F1 = np.zeros((4, 4, 3), np.uint8)
F2 = np.ones((4, 4, 3), np.uint8)


def test_empty_buffer():
    fb = FrameBuffer()
    assert fb.seq == 0 and fb.get() == (None, 0)


def test_put_get_monotonic_seq():
    fb = FrameBuffer()
    assert fb.put(F1) == 1, "put returns seq 1"
    got, gseq = fb.get()
    assert gseq == 1 and got is F1, "get returns latest + seq"
    fb.put(F2)
    assert fb.seq == 2 and fb.get()[0] is F2, "seq advances monotonically"


def test_wait_newer_returns_fresh_frame():
    fb = FrameBuffer()
    fb.put(F1)
    threading.Timer(0.05, lambda: fb.put(F2)).start()
    t0 = time.time()
    frame, seq = fb.wait_newer(1, timeout_s=2.0)
    assert seq == 2 and frame is F2 and time.time() - t0 < 1.0


def test_wait_newer_times_out_loudly():
    fb = FrameBuffer()
    fb.put(F1)
    try:
        fb.wait_newer(99, timeout_s=0.1)
        assert False, "must raise TimeoutError"
    except TimeoutError:
        pass


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
