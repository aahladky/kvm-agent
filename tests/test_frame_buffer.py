"""
test_frame_buffer.py — OFFLINE test for the frame-freshness store (finding #6:
no guarantee a post-action frame was captured after the action).

    python tests/test_frame_buffer.py
"""
import sys, os, time, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from kvm_agent.hardware.env import FrameBuffer


def test_put_get_seq():
    fb = FrameBuffer()
    assert fb.seq == 0 and fb.get() == (None, 0), "empty buffer seq 0, frame None"

    f1 = np.zeros((4, 4, 3), np.uint8)
    s1 = fb.put(f1)
    assert s1 == 1, "put returns seq 1"
    got, gseq = fb.get()
    assert gseq == 1 and got is f1, "get returns latest + seq"

    f2 = np.ones((4, 4, 3), np.uint8)
    fb.put(f2)
    assert fb.seq == 2 and fb.get()[0] is f2, "seq advances monotonically"


# wait_newer returns promptly once a newer frame lands (producer on another thread)
def test_wait_newer_returns_newer_frame():
    f1 = np.zeros((4, 4, 3), np.uint8)
    f2 = np.ones((4, 4, 3), np.uint8)
    fb2 = FrameBuffer()
    fb2.put(f1)
    threading.Timer(0.05, lambda: fb2.put(f2)).start()
    t0 = time.time()
    frame, seq = fb2.wait_newer(1, timeout_s=2.0)
    assert seq == 2 and frame is f2 and time.time() - t0 < 1.0, "wait_newer returns the newer frame"


# wait_newer times out loudly when nothing newer arrives
def test_wait_newer_times_out():
    fb2 = FrameBuffer()
    try:
        fb2.wait_newer(99, timeout_s=0.1)
        assert False, "wait_newer times out with TimeoutError"
    except TimeoutError:
        pass


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
