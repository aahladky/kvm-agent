"""
test_hard_constraints.py — OFFLINE tests for HARD recalled-fact enforcement (retrieval -> code).

No server / no rig. Covers: classify_facts (prohibition/breakage cues -> directives + enforceable
gates vs soft world facts), _gate_target, _memory_block ordering, the executive constraint gate
(_blocked_by_constraint + set_constraints + run_step), the _arm_memory/_disarm_memory orchestration
(directives-first prompt + executive gates, fail-soft), and HindsightMemory.recall_constraints.

    python tests\test_hard_constraints.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kvm_agent.orchestration.executive as exe
from kvm_agent.orchestration.executive import Executive
from kvm_agent.orchestration.planner import _memory_block, _arm_memory, _disarm_memory, Planner
from kvm_agent.memory.hindsight import classify_facts, _gate_target, HindsightMemory

exe.time.sleep = lambda *a, **k: None

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)

def boom(*a, **k):
    raise RuntimeError("server down")


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


def make_ex(text="", vision=None):
    return Executive(FakeEnv(), FakeAgent(), verifier=FakeVerifier(text, vision), capture=False)


# 1. classify_facts: breakage/prohibition -> directive (+gate); world fact -> soft -----------------
ff = "The Firefox Start shortcut is broken; launching Firefox by name opens a Problem with Shortcut dialog."
d, soft, g = classify_facts([ff])
check("classify: breakage fact -> a directive", len(d) == 1 and d[0] == ff)
check("classify: breakage fact -> no soft entry", soft == [])
check("classify: derives a launch gate on the app",
      len(g) == 1 and g[0]["op"] == "launch" and g[0]["match"] == "firefox" and g[0]["reason"] == ff)

d, soft, g = classify_facts(["The default browser on this machine is Google Chrome."])
check("classify: neutral world fact -> soft, not a directive", d == [] and len(soft) == 1 and g == [])

d, soft, g = classify_facts(["Do not use winget; it is unavailable on this box."])
check("classify: prohibition with no parseable op/target -> directive only",
      len(d) == 1 and g == [])

d, soft, g = classify_facts(["Never launch Edge on this machine."])
check("classify: 'never launch Edge' -> gate {launch, edge}",
      len(d) == 1 and g and g[0]["op"] == "launch" and g[0]["match"] == "edge")

d, soft, g = classify_facts(["Avoid clicking 'Set as default' during first run."])
check("classify: quoted target -> gate {click, 'set as default'}",
      g and g[0]["op"] == "click" and g[0]["match"] == "set as default")

d, soft, g = classify_facts(["", "   "])
check("classify: blanks ignored", d == [] and soft == [] and g == [])

# REGRESSION (live 2026-06-22): a quoted CONSEQUENCE (the dialog name) must NOT become the gate
# target — the gate must forbid launching the APP. This is the exact fact that slipped through live.
live = ("On the Windows 10 test machine, the Firefox Start-menu shortcut is broken, pointing to a "
        "moved private_browsing.exe file, which causes a 'Problem with Shortcut' dialog to appear "
        "instead of launching Firefox.")
d, soft, g = classify_facts([live])
check("classify(live FF fact): gate targets the app, not the quoted dialog name",
      len(g) == 1 and g[0]["op"] == "launch" and g[0]["match"] == "firefox")
exg = make_ex(); exg.set_constraints(g)
check("classify(live FF fact): blocks 'launch Firefox'",
      exg._blocked_by_constraint({"op": "launch", "app": "Firefox"}) is not None)
check("classify(live FF fact): does NOT match the dialog phrase",
      exg._blocked_by_constraint({"op": "click", "target": "Problem with Shortcut"}) is None)


# 2. _gate_target ---------------------------------------------------------------------------------
check("gate_target: prefers a quoted name", _gate_target("launching 'Mozilla Firefox' fails") == "Mozilla Firefox")
check("gate_target: else first proper-noun", _gate_target("launching Firefox by name is broken") == "Firefox")
check("gate_target: none when no name", _gate_target("the shortcut is broken") is None)


# 3. _memory_block: HARD CONSTRAINTS first, then soft facts ----------------------------------------
blk = _memory_block(["don't launch Firefox by name — it's broken"], ["the default browser is Chrome"])
check("memory_block: has both sections", "HARD CONSTRAINTS" in blk and "RELEVANT MEMORY" in blk)
check("memory_block: constraints come FIRST", blk.index("HARD CONSTRAINTS") < blk.index("RELEVANT MEMORY"))
check("memory_block: facts-only -> no HARD header", "HARD CONSTRAINTS" not in _memory_block([], ["x"]))
check("memory_block: directives-only -> no soft header", "RELEVANT MEMORY" not in _memory_block(["x"], []))
check("memory_block: empty -> ''", _memory_block([], []) == "")


# 4. Executive gate: _blocked_by_constraint + set_constraints --------------------------------------
ex = make_ex()
check("gate: default (no constraints) -> nothing blocked", ex.hard_constraints == [] and
      ex._blocked_by_constraint({"op": "launch", "app": "Firefox"}) is None)
ex.set_constraints([{"op": "launch", "match": "firefox", "reason": "broken shortcut"}])
check("gate: launch Firefox blocked", ex._blocked_by_constraint({"op": "launch", "app": "Firefox"}) == "broken shortcut")
check("gate: launch notepad not blocked", ex._blocked_by_constraint({"op": "launch", "app": "notepad"}) is None)
check("gate: click Firefox not blocked (op mismatch)",
      ex._blocked_by_constraint({"op": "click", "target": "Firefox"}) is None)
check("gate: full-path firefox launch NOT blocked (bypasses the broken Start shortcut)",
      ex._blocked_by_constraint({"op": "launch", "app": r"C:\Program Files\Mozilla Firefox\firefox.exe"}) is None)
check("gate: ms-settings URI launch NOT blocked",
      ex._blocked_by_constraint({"op": "launch", "app": "ms-settings:defaultapps"}) is None)
ex.set_constraints([{"op": None, "match": "secret"}])
check("gate: op=None matches any op",
      ex._blocked_by_constraint({"op": "type", "text": "my secret token"}) is not None)


# 5. run_step honours the gate: a blocked launch never reaches the HID ------------------------------
ex = make_ex()
ex.set_constraints([{"op": "launch", "match": "firefox", "reason": "the Firefox shortcut is broken"}])
res = ex.run_step({"op": "launch", "app": "Firefox"})
check("run_step gate: status failed@0:launch", res["status"] == "failed@0:launch")
check("run_step gate: rec carries the block reason", res["rec"].get("blocked") == "the Firefox shortcut is broken")
check("run_step gate: failure_summary explains the block", "blocked by hard constraint" in res.get("failure_summary", ""))
check("run_step gate: no Win+R / Start keystrokes were sent",
      ("combo", "win+r") not in ex.env.r4.calls and ("key", "win") not in ex.env.r4.calls)

# gate OFF (default) -> the same launch proceeds to the primitive
ex2 = make_ex()
called = []
ex2.launch = lambda app, **k: called.append(app) or True
res2 = ex2.run_step({"op": "launch", "app": "Firefox"})
check("run_step no-gate: launch runs", res2["status"] == "ok" and called == ["Firefox"])


# 6. _arm_memory / _disarm_memory: directives-first prompt + executive gates, fail-soft ------------
class FakeMem:
    def __init__(self, c): self._c = c
    def recall_constraints(self, goal): return self._c
    def recall_block(self, goal): return "SOFT BLOCK"

p, ex = Planner(), make_ex()
mem = FakeMem({"directives": ["don't launch Firefox by name — broken"],
               "facts": ["the default browser is Chrome"],
               "gates": [{"op": "launch", "match": "firefox", "reason": "broken"}]})
n = _arm_memory(p, ex, mem, "set default to firefox")
check("arm: returns the gate count", n == 1)
check("arm: planner.context leads with HARD CONSTRAINTS", (p.context or "").startswith("HARD CONSTRAINTS"))
check("arm: planner.context also has the soft facts", "RELEVANT MEMORY" in (p.context or ""))
check("arm: executive gates set", ex.hard_constraints == mem._c["gates"])
_disarm_memory(p, ex)
check("disarm: context cleared", p.context is None)
check("disarm: gates cleared", ex.hard_constraints == [])

# fail-soft: recall_constraints raises -> fall back to recall_block, no gates
class BoomMem:
    def recall_constraints(self, g): raise RuntimeError("down")
    def recall_block(self, g): return "SOFT ONLY"
p2, ex2 = Planner(), make_ex()
_arm_memory(p2, ex2, BoomMem(), "g")
check("arm fail-soft: falls back to soft block", p2.context == "SOFT ONLY" and ex2.hard_constraints == [])

# total outage: both raise -> no context, no gates, no exception
class DeadMem:
    def recall_constraints(self, g): raise RuntimeError("x")
    def recall_block(self, g): raise RuntimeError("y")
p3, ex3 = Planner(), make_ex()
_arm_memory(p3, ex3, DeadMem(), "g")
check("arm total-outage: context None, gates empty", p3.context is None and ex3.hard_constraints == [])


# 7. HindsightMemory.recall_constraints: recall -> classify ----------------------------------------
m = HindsightMemory(base_url="http://x", bank="B")
m._post = lambda path, body: {"results": [
    {"text": "The Firefox Start shortcut is broken; launching Firefox opens a Problem with Shortcut dialog."},
    {"text": "The default browser is Google Chrome."}]}
c = m.recall_constraints("set firefox default")
check("recall_constraints: 1 directive, 1 fact, 1 gate",
      len(c["directives"]) == 1 and len(c["facts"]) == 1 and len(c["gates"]) == 1)
check("recall_constraints: gate targets the firefox launch",
      c["gates"][0]["op"] == "launch" and c["gates"][0]["match"] == "firefox")
m._post = boom
c = m.recall_constraints("q")
check("recall_constraints: fail-soft -> empty fields",
      c == {"directives": [], "facts": [], "gates": []})


print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
