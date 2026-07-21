"""
test_target.py — OFFLINE test for the manual power/reset seam.

    python tests/test_target.py
"""
import sys, os, builtins
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.hardware import target

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)

calls = []
real_input = builtins.input
builtins.input = lambda prompt="": calls.append(prompt) or ""
try:
    target.reboot()
finally:
    builtins.input = real_input
check("reboot() blocks on operator confirmation exactly once", len(calls) == 1)
check("reboot() prompt tells the operator what to do", "power-cycle" in calls[0].lower())
check("is_up() is True after operator confirmation (v1 contract)", target.is_up() is True)


# --- verify_hid gate (fake r4 + fake cam; verifies the camera-checked round-trips) ---
import cv2
import numpy as np


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


r4 = FakeR4()
cam = FakeCam([SAME, CHANGED, SAME, CHANGED])
ok, detail = target.verify_hid(r4, cam)
check("gate passes when both round-trips show change", ok is True)
check("gate ran the keyboard round-trip (win+r + esc)",
      ("combo", "win+r") in r4.calls and ("key", "esc") in r4.calls)
check("gate ran the mouse round-trip (Start click)",
      ("move", 20, 1055) in r4.calls and ("click",) in r4.calls)

r4_dead = FakeR4()
cam_dead = FakeCam([SAME, SAME, SAME, SAME])
ok_dead, detail_dead = target.verify_hid(r4_dead, cam_dead)
check("gate fails closed when nothing changes", ok_dead is False)
check("gate names the dead collection (keyboard first)", "keyboard" in detail_dead)

r4_mdead = FakeR4()
cam_mdead = FakeCam([SAME, CHANGED, SAME, SAME])
ok_mdead, detail_mdead = target.verify_hid(r4_mdead, cam_mdead)
check("gate catches the half-dead case (mouse only)",
      ok_mdead is False and "mouse" in detail_mdead)

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
