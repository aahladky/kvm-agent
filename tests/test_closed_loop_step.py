"""
test_closed_loop_step.py — OFFLINE tests for the PER-STEP closed loop (run_goal_step) + its parsing.

No rig: env/agent/verifier faked, the planner is a scripted/capturing fake, HID recorded. Covers the
single-object parser (_extract_step / _first_json_object), per-step lint (validate_step),
Planner.next_step prompt shape, and the run_goal_step loop control (happy path, premature-'done'
guard, invalid-action rejection, stuck limit, and the executive hard-constraint gate firing in-loop).

    python tests\test_closed_loop_step.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kvm_agent.orchestration.executive as exe
from kvm_agent.orchestration.executive import Executive
from kvm_agent.orchestration.planner import (
    _extract_step, _first_json_object, validate_step, run_goal_step, Planner)

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


class ScriptPlanner(Planner):
    """Returns a canned sequence of single steps; ignores the screen. Records the histories it saw."""
    def __init__(self, steps):
        self.steps = list(steps)
        self.i = 0
        self.seen_history = []
    def next_step(self, goal, screen_png=None, history=None):
        self.seen_history.append(list(history or []))
        s = self.steps[self.i] if self.i < len(self.steps) else {"op": "done"}
        self.i += 1
        return s


# 1. _extract_step: object / array / reasoning / fence / prose / garbage / brace-in-string ---------
check("extract_step: bare object",
      _extract_step('{"op":"launch","app":"notepad"}') == {"op": "launch", "app": "notepad"})
check("extract_step: 1-element array -> first",
      _extract_step('[{"op":"done"}]') == {"op": "done"})
check("extract_step: strips <think> with brackets",
      _extract_step('<think>maybe click [x]</think>\n{"op":"tap","key":"enter"}')["op"] == "tap")
check("extract_step: ```json fence",
      _extract_step('```json\n{"op":"type","text":"hi"}\n```')["text"] == "hi")
check("extract_step: object embedded in prose",
      _extract_step('Sure — the next action is {"op":"click","target":"Save"} now.')["target"] == "Save")
check("extract_step: garbage -> done",
      _extract_step("there is no json here") == {"op": "done"})
check("extract_step: brace inside a string value is preserved",
      _extract_step('{"op":"type","text":"a } b"}')["text"] == "a } b")
check("first_json_object: ignores a leading stray brace-in-quote",
      _first_json_object('note "}" then {"op":"done"}') == {"op": "done"})


# 2. validate_step: per-step lint (mirrors validate_plan rules) ------------------------------------
s, iss = validate_step({"op": "launch", "app": "cmd"})
check("validate_step: valid launch passes", s == {"op": "launch", "app": "cmd"} and iss == [])
s, iss = validate_step({"op": "frobnicate"})
check("validate_step: unknown op -> None", s is None and iss)
s, iss = validate_step({"op": "verify", "expect": "Chrome is now the default browser"})
check("validate_step: claim expect -> ask", s.get("op") == "verify" and "ask" in s)
s, iss = validate_step({"op": "verify", "expect": "61"})
check("validate_step: short literal expect kept", s == {"op": "verify", "expect": "61"} and iss == [])
s, iss = validate_step({"op": "click", "target": ""})
check("validate_step: click with no target -> None", s is None)
s, iss = validate_step({"op": "done"})
check("validate_step: done passes", s == {"op": "done"})
s, iss = validate_step({"op": "type"})
check("validate_step: type missing text -> None", s is None)
s, iss = validate_step("not a dict")
check("validate_step: non-dict -> None", s is None)


# 3. Planner.next_step: builds the single-action prompt, parses the reply --------------------------
class CapturePlanner(Planner):
    def __init__(self): self.captured = None
    def _complete(self, user_msg, screen_png=None):
        self.captured = user_msg
        return '{"op":"launch","app":"notepad"}'

cp = CapturePlanner()
step = cp.next_step("Open Notepad", b"PNG", ["launch cmd -> ok"])
check("next_step: parses a single object", step == {"op": "launch", "app": "notepad"})
check("next_step: prompt carries the GOAL", "GOAL: Open Notepad" in cp.captured)
check("next_step: prompt lists ACTIONS SO FAR", "ACTIONS SO FAR" in cp.captured and "launch cmd -> ok" in cp.captured)
check("next_step: asks for ONE object, not an array", "NOT an array" in cp.captured)
cp2 = CapturePlanner()
cp2.next_step("g", None, None)
check("next_step: no history -> no ACTIONS SO FAR block", "ACTIONS SO FAR" not in cp2.captured)


# 4. run_goal_step: happy path (observe -> act -> observe -> done) ---------------------------------
ex = make_ex()
ex.launch = lambda app, **k: True
pl = ScriptPlanner([{"op": "launch", "app": "notepad"}, {"op": "done"}])
res = run_goal_step("Open Notepad", pl, ex, reset_first=False)
check("happy: status done", res["status"] == "done")
check("happy: one action executed", res["steps"] == 1 and res["trace"] == [{"op": "launch", "app": "notepad"}])
check("happy: loop tag is per-step", res["loop"] == "per-step")
check("happy: history records the ok step", any("launch notepad -> ok" in h for h in res["history"]))


# 5. premature 'done' guard: a 'done' before any action is ignored ONCE ----------------------------
ex = make_ex()
ex.launch = lambda app, **k: True
pl = ScriptPlanner([{"op": "done"}, {"op": "launch", "app": "notepad"}, {"op": "done"}])
res = run_goal_step("Open Notepad", pl, ex, reset_first=False)
check("premature-done: still completes", res["status"] == "done")
check("premature-done: the launch ran after the nudge", res["steps"] == 1)
check("premature-done: nudge recorded in history",
      any("no action has been taken yet" in h for h in res["history"]))


# 6. invalid action: rejected (not executed) then recovers ----------------------------------------
ex = make_ex()
ex.launch = lambda app, **k: True
pl = ScriptPlanner([{"op": "frobnicate"}, {"op": "launch", "app": "notepad"}, {"op": "done"}])
res = run_goal_step("Open Notepad", pl, ex, reset_first=False)
check("invalid: completes after rejecting the bad op", res["status"] == "done")
check("invalid: bad op was NOT counted as an executed step", res["steps"] == 1)
check("invalid: rejection noted in history", any("INVALID action" in h for h in res["history"]))


# 7. stuck: repeated failures hit the stuck limit -------------------------------------------------
ex = make_ex()
ex.launch = lambda app, **k: False          # every launch fails
pl = ScriptPlanner([{"op": "launch", "app": "x"}] * 6)
res = run_goal_step("do x", pl, ex, reset_first=False, stuck_limit=3)
check("stuck: status stuck", res["status"] == "stuck")
check("stuck: stopped at the limit", res["steps"] == 3)


# 8. executive hard-constraint gate fires IN-LOOP (retrieval -> enforcement) -----------------------
ex = make_ex()
launched = []
ex.launch = lambda app, **k: launched.append(app) or True   # should never be reached
ex.set_constraints([{"op": "launch", "match": "firefox", "reason": "the Firefox shortcut is broken"}])
pl = ScriptPlanner([{"op": "launch", "app": "Firefox"}] * 5)
res = run_goal_step("open firefox", pl, ex, reset_first=False, stuck_limit=3)
check("gate-in-loop: blocked launch never executed", launched == [])
check("gate-in-loop: run ends stuck (kept getting blocked)", res["status"] == "stuck")
check("gate-in-loop: block reason fed into history",
      any("blocked by hard constraint" in h for h in res["history"]))
check("gate-in-loop: no Win+R / Start sent for the blocked app",
      ("combo", "win+r") not in ex.env.r4.calls and ("key", "win") not in ex.env.r4.calls)


# 9. planner-error is caught, not raised ----------------------------------------------------------
class BoomPlanner(Planner):
    def next_step(self, goal, screen_png=None, history=None):
        raise RuntimeError("model down")

res = run_goal_step("g", BoomPlanner(), make_ex(), reset_first=False)
check("planner-error: surfaced as status, not an exception", res["status"] == "planner-error")


print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
