"""
test_closed_loop.py — OFFLINE tests for the closed-loop guards (2026-06-21).

No rig: env/agent/verifier faked, HID recorded. Covers the blocking-error-dialog detector + the
pre-click auto-dismiss guard, and the optional per-step precondition gate.

    python tests\test_closed_loop.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kvm_agent.orchestration.executive as exe
from kvm_agent.orchestration.executive import Executive

exe.time.sleep = lambda *a, **k: None

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)


class FakeR4:
    def __init__(self): self.calls = []
    def key(self, k): self.calls.append(("key", k))
    def combo(self, c): self.calls.append(("combo", c))
    def type(self, t): self.calls.append(("type", t))
    def move(self, x, y): self.calls.append(("move", (x, y)))
    def click(self): self.calls.append(("click", None))
    def scroll(self, t): self.calls.append(("scroll", t))


class FakeEnv:
    def __init__(self): self.r4 = FakeR4()
    def observe(self): return {"screenshot": b"PNG"}


class FakeVerifier:
    def __init__(self, text="", vision=None): self.text, self.vision = text, vision
    def read_text(self, png): return self.text
    def _vision(self, png, prompt): return self.vision


class FakeAgent:
    pass


def make_ex(text="", vision=None, guard=True):
    return Executive(FakeEnv(), FakeAgent(), verifier=FakeVerifier(text, vision),
                     capture=False, guard_dialogs=guard)


# 1. _blocking_dialog: error text -> True, normal -> False ------------------------------------
check("blocking: 'problem with shortcut' -> True",
      make_ex("x Firefox Problem with Shortcut this shortcut will no longer work")._blocking_dialog() is True)
check("blocking: 'cannot find' -> True",
      make_ex("Windows cannot find 'Firefox'. Make sure you typed")._blocking_dialog() is True)
check("blocking: normal Settings screen -> False",
      make_ex("Settings  Default apps  Web browser  Google Chrome")._blocking_dialog() is False)

# 2. pre-click guard: a blocking dialog is Esc'd before the click -----------------------------
ex = make_ex(text="Problem with Shortcut: this shortcut will no longer work")
ex.click_target = lambda t, **k: (True, (10, 10))     # click 'succeeds' after the guard
res = ex.run_plan([{"op": "click", "target": "OK"}, {"op": "done"}], goal="t")
check("guard: run completes (done)", res["status"] == "done")
check("guard: Esc sent before the click", ("key", "esc") in ex.env.r4.calls)
crec = [r for r in res["log"] if r["op"] == "click"][0]
check("guard: dismissed_dialog recorded", crec.get("dismissed_dialog") is True)

# guard does NOT fire on a normal screen
ex = make_ex(text="Settings Default apps Web browser")
ex.click_target = lambda t, **k: (True, (10, 10))
ex.run_plan([{"op": "click", "target": "Firefox"}, {"op": "done"}], goal="t")
check("guard: no Esc on a normal screen", ("key", "esc") not in ex.env.r4.calls)

# guard disabled (guard_dialogs=False) -> no Esc even with an error dialog
ex = make_ex(text="Problem with Shortcut: this shortcut will no longer work", guard=False)
ex.click_target = lambda t, **k: (True, (10, 10))
ex.run_plan([{"op": "click", "target": "OK"}, {"op": "done"}], goal="t")
check("guard off -> no Esc", ("key", "esc") not in ex.env.r4.calls)

# 3. precondition gate: confirm False -> step FAILS without acting ----------------------------
ex = make_ex(vision="no")          # confirm() -> "no" -> False
called = {"n": 0}
ex.click_target = lambda t, **k: (called.update(n=called["n"] + 1), (True, (1, 1)))[1]
res = ex.run_plan([{"op": "click", "target": "Firefox",
                    "precondition": "the Settings app is open"}, {"op": "done"}], goal="t")
check("precondition unmet -> failed@0:click", res["status"] == "failed@0:click")
check("precondition unmet -> click_target NOT called", called["n"] == 0)
check("precondition unmet -> no click sent", ("click", None) not in ex.env.r4.calls)

# precondition met -> proceeds
ex = make_ex(vision="yes")
ex.click_target = lambda t, **k: (True, (1, 1))
res = ex.run_plan([{"op": "click", "target": "Firefox",
                    "precondition": "the Settings app is open"}, {"op": "done"}], goal="t")
check("precondition met -> run completes (done)", res["status"] == "done")

# precondition with no vision verifier (None) -> fail-open (does not block)
ex = make_ex(vision=None)
ex.click_target = lambda t, **k: (True, (1, 1))
res = ex.run_plan([{"op": "click", "target": "Firefox",
                    "precondition": "the Settings app is open"}, {"op": "done"}], goal="t")
check("precondition fail-open (no verifier) -> done", res["status"] == "done")

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
