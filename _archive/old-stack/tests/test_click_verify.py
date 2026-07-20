"""
test_click_verify.py — OFFLINE tests for pre-click ground verification (2026-06-21).

No rig: vision + grounding are faked, HID calls recorded. The key assertion is PREVENTION — when
the grounded point can't be confirmed as the target, NO click is sent (vs the old behavior of firing
a confident wrong-state click and accepting it because pixels moved).

    python tests\test_click_verify.py
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
    def move(self, x, y): self.calls.append(("move", (x, y)))
    def click(self): self.calls.append(("click", None))
    def key(self, k): self.calls.append(("key", k))
    def combo(self, c): self.calls.append(("combo", c))
    def type(self, t): self.calls.append(("type", t))


class FakeEnv:
    def __init__(self): self.r4 = FakeR4()
    def observe(self): return {"screenshot": b"PNG"}


class FakeVerifier:
    def __init__(self, ans): self.ans = ans
    def _vision(self, png, prompt): return self.ans
    def read_text(self, png): return ""


class FakeAgent:
    pass


def make_ex(vision_ans=None):
    return Executive(FakeEnv(), FakeAgent(), verifier=FakeVerifier(vision_ans), capture=False)


def attach_ground(ex, xy=(300, 400)):
    def g(target):
        ex.last_ground = {"target": target, "attempts": [], "xy": xy}
        return xy, f"click{xy}"
    ex.ground = g


# 1. _ground_ok: vision yes/no, and FAIL-OPEN on None -----------------------------------------
check("_ground_ok: vision 'yes' -> True", make_ex("yes")._ground_ok("X", (10, 10), b"PNG") is True)
check("_ground_ok: vision 'no' -> False", make_ex("no")._ground_ok("X", (10, 10), b"PNG") is False)
check("_ground_ok: vision None -> True (fail-open)",
      make_ex(None)._ground_ok("X", (10, 10), b"PNG") is True)

# 2. PREVENTION: an unverifiable ground sends NO click ----------------------------------------
ex = make_ex("no")        # vision says the grounded point is NOT the target
attach_ground(ex)
res = ex.click_target("Firefox", retries=1)
check("rejected ground -> click_target returns (False, None)", res == (False, None))
check("rejected ground -> NO click sent", ("click", None) not in ex.env.r4.calls)
check("rejected ground -> cursor never moved to click", not any(c[0] == "move" for c in ex.env.r4.calls))
check("rejected ground -> last_ground.verified == False", ex.last_ground.get("verified") is False)

# 3. verified ground -> clicks and (with an effect) succeeds -----------------------------------
ex = make_ex("yes")
attach_ground(ex)
ex._click_effect = lambda b, a, xy: True
res = ex.click_target("the Set default button", retries=1)
check("verified ground -> returns (True, xy)", res == (True, (300, 400)))
check("verified ground -> click WAS sent", ("click", None) in ex.env.r4.calls)
check("verified ground -> last_ground.verified == True", ex.last_ground.get("verified") is True)

# 4. fail-open: no vision verifier -> behaves like before (does NOT block the click) -----------
ex = make_ex(None)        # _vision returns None -> _ground_ok fail-open True
attach_ground(ex)
ex._click_effect = lambda b, a, xy: True
res = ex.click_target("anything", retries=0)
check("no verifier (fail-open) -> click still sent", ("click", None) in ex.env.r4.calls)
check("no verifier (fail-open) -> succeeds", res == (True, (300, 400)))

# 5. failure summary surfaces 'not on screen' so the planner can scroll/replan -----------------
ex = make_ex(None)
rec = {"i": 11, "op": "click", "step": {"op": "click", "target": "Firefox"},
       "ground": {"xy": (543, 784), "verified": False}, "ok": False}
s = ex._failure_summary(rec)
check("failure summary: says NOT found", "NOT found" in s)
check("failure summary: suggests scrolling", "scroll" in s.lower())

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
