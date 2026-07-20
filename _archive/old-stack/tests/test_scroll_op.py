"""
test_scroll_op.py — OFFLINE tests for the general `scroll` plan op (2026-06-21).

No rig: the HID calls are recorded on a fake R4. Covers schema acceptance, the no-op guard
(scroll alone is not an actionable goal step), direction->wheel-sign + center-park, and run_plan
routing.

    python tests\test_scroll_op.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kvm_agent.orchestration.executive as exe
from kvm_agent.orchestration.executive import Executive
from kvm_agent.orchestration.planner import validate_plan, plan_is_actionable

exe.time.sleep = lambda *a, **k: None

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)


class FakeR4:
    def __init__(self): self.calls = []
    def combo(self, c): self.calls.append(("combo", c))
    def key(self, k): self.calls.append(("key", k))
    def type(self, t): self.calls.append(("type", t))
    def move(self, x, y): self.calls.append(("move", (x, y)))
    def click(self): self.calls.append(("click", None))
    def scroll(self, t): self.calls.append(("scroll", t))


class FakeEnv:
    def __init__(self): self.r4 = FakeR4()
    def observe(self): return {"screenshot": b"PNG"}


class FakeVerifier:
    def read_text(self, png): return ""
    def _vision(self, *a, **k): return "no"


class FakeAgent:
    pass


def make_ex():
    return Executive(FakeEnv(), FakeAgent(), verifier=FakeVerifier(), capture=False)


# 1. validate_plan keeps a scroll op (not dropped as unknown) ----------------------------------
clean, issues = validate_plan([{"op": "scroll", "direction": "down", "amount": 3},
                               {"op": "click", "target": "Firefox"}, {"op": "done"}])
check("validate_plan keeps scroll", any(s.get("op") == "scroll" for s in clean))
check("validate_plan: bare scroll (no fields) survives",
      any(s.get("op") == "scroll" for s in validate_plan([{"op": "scroll"}, {"op": "done"}])[0]))

# 2. guard: scroll is NOT an actionable goal step (like sleep/verify) --------------------------
check("scroll-only plan is NOT actionable",
      plan_is_actionable([{"op": "scroll", "direction": "down"}, {"op": "done"}]) is False)
check("scroll + click IS actionable",
      plan_is_actionable([{"op": "scroll"}, {"op": "click", "target": "x"}]) is True)

# 3. Executive.scroll: direction -> wheel sign, parks cursor at center -------------------------
ex = make_ex()
ex.scroll("down", 5)
check("scroll down -> negative wheel", ("scroll", -5) in ex.env.r4.calls)
check("scroll parks cursor at screen center", ("move", (960, 540)) in ex.env.r4.calls)

ex = make_ex(); ex.scroll("up", 2)
check("scroll up -> positive wheel", ("scroll", 2) in ex.env.r4.calls)

ex = make_ex(); ex.scroll()
check("scroll default -> down 3", ("scroll", -3) in ex.env.r4.calls)

ex = make_ex(); ex.scroll("down", -4)   # magnitude only; sign comes from direction
check("scroll ignores negative amount sign", ("scroll", -4) in ex.env.r4.calls)

# 4. run_plan routes the scroll op -------------------------------------------------------------
ex = make_ex()
res = ex.run_plan([{"op": "scroll", "direction": "down", "amount": 2}, {"op": "done"}], goal="t")
check("run_plan scroll then done -> done", res["status"] == "done")
check("run_plan issued the wheel notch", ("scroll", -2) in ex.env.r4.calls)

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
