"""
test_boot_gate.py — OFFLINE test: boot() runs the camera-verified HID gate by default
and fails LOUD + re-runnable (review 2026-07-21 P0-4: the gate only protected the
battery; REPL sessions drove an unverified channel -- the exact "probe flags LIE"
case verify_hid exists for).

    python tests/test_boot_gate.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_loop_holo

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)


class FakeR4:
    def __init__(self):
        self.calls = []
    def set_screen(self, w, h):
        self.calls.append(("set_screen", w, h))

class FakeEnv:
    def __init__(self, cam_index=0, screen_size=(1920, 1080), show=False):
        self.screen_width, self.screen_height = screen_size
        self.r4 = FakeR4()
        self.cam = object()
    def close(self):
        pass

gate_calls = []

def gate_fail(r4, cam, screen=(1920, 1080), attempts=2, **kw):
    gate_calls.append({"screen": screen, "attempts": attempts})
    return False, "keyboard NOT delivering (win+r diff 0.0 <= 20.0)"

def gate_pass(r4, cam, screen=(1920, 1080), attempts=2, **kw):
    gate_calls.append({"screen": screen, "attempts": attempts})
    return True, "hid ok"


_saved = (agent_loop_holo.PicoEnv, agent_loop_holo.ENV, agent_loop_holo.verify_hid)
agent_loop_holo.PicoEnv = FakeEnv
agent_loop_holo.ENV = None
try:
    # --- gate failure: loud raise, hardware released, boot() re-runnable ---
    agent_loop_holo.verify_hid = gate_fail
    try:
        agent_loop_holo.boot()
        raised = None
    except RuntimeError as e:
        raised = str(e)
    check("gate failure raises RuntimeError", raised is not None)
    check("the raise carries the gate's diagnosis", raised and "keyboard NOT delivering" in raised)
    check("failed boot leaves ENV reset (re-runnable)", agent_loop_holo.ENV is None)
    check("boot gate is single-attempt (non-interactive)",
          gate_calls and gate_calls[0]["attempts"] == 1)

    # --- gate pass: boot completes ---
    agent_loop_holo.verify_hid = gate_pass
    check("boot() returns True when the gate passes", agent_loop_holo.boot() is True)
    check("gate ran against the env's screen space",
          gate_calls[-1]["screen"] == (1920, 1080))

    # --- verify=False skips the gate entirely ---
    agent_loop_holo.ENV = None
    agent_loop_holo.verify_hid = gate_fail
    n_before = len(gate_calls)
    check("boot(verify=False) skips the gate",
          agent_loop_holo.boot(verify=False) is True and len(gate_calls) == n_before)
finally:
    agent_loop_holo.PicoEnv, agent_loop_holo.ENV, agent_loop_holo.verify_hid = _saved

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
