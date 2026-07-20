"""
test_memory.py — OFFLINE tests for the Hindsight memory client + planner injection (2026-06-21).

No server: HindsightMemory._post is stubbed. Covers recall parsing, recall_block formatting/dedup,
retain request shape, FAIL-SOFT behavior (a memory outage must never break a run), and the planner
_inject hook.

    python tests\test_memory.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.memory.hindsight import HindsightMemory
from kvm_agent.orchestration.planner import Planner

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)


def boom(*a, **k):
    raise RuntimeError("server down")


# 1. recall: parse result texts ---------------------------------------------------------------
m = HindsightMemory(base_url="http://x", bank="B")
m._post = lambda path, body: {"results": [{"text": "fact one", "type": "world"},
                                          {"text": "fact two", "type": "experience"},
                                          {"text": "fact one", "type": "observation"}]}
check("recall returns the result texts", m.recall("q") == ["fact one", "fact two", "fact one"])

# 2. recall_block: header + de-dup ------------------------------------------------------------
block = m.recall_block("q")
check("recall_block has the header", "RELEVANT MEMORY" in block)
check("recall_block de-dups repeats", block.count("- fact one") == 1)
check("recall_block keeps distinct facts", "- fact two" in block)

# 3. recall hits the right endpoint -----------------------------------------------------------
seen = {}
m._post = lambda path, body: seen.update(path=path, body=body) or {"results": []}
m.recall("hello")
check("recall posts to /memories/recall", seen["path"].endswith("/banks/B/memories/recall"))
check("recall sends the query", seen["body"]["query"] == "hello")

# 4. FAIL-SOFT: any error -> empty (never raises into a run) ----------------------------------
m._post = boom
check("recall fail-soft -> []", m.recall("q") == [])
check("recall_block fail-soft -> ''", m.recall_block("q") == "")

# 5. retain: batch {items:[...]} shape + success ----------------------------------------------
cap = {}
m._post = lambda path, body: cap.update(path=path, body=body) or {"success": True, "items_count": 1}
ok = m.retain("a new fact", context="ctx", tags=["win"])
check("retain returns True on success", ok is True)
check("retain wraps content in items[]", cap["body"]["items"][0]["content"] == "a new fact")
check("retain passes context", cap["body"]["items"][0]["context"] == "ctx")
check("retain is synchronous (async=False)", cap["body"]["async"] is False)
m._post = boom
check("retain fail-soft -> False", m.retain("x") is False)

# 6. empty recall -> empty block --------------------------------------------------------------
m._post = lambda path, body: {"results": []}
check("recall_block empty -> ''", m.recall_block("q") == "")

# 7. planner _inject: prepends armed context, else unchanged ----------------------------------
p = Planner()
check("_inject with no context -> unchanged", p._inject("GOAL: x") == "GOAL: x")
p.context = "RELEVANT MEMORY:\n- the default browser is Chrome"
inj = p._inject("GOAL: set default to Firefox")
check("_inject prepends the memory block", inj.startswith("RELEVANT MEMORY:\n- the default browser is Chrome\n\nGOAL:"))
check("_inject keeps the original goal", inj.endswith("GOAL: set default to Firefox"))

# 8. write-back: _plan_to_text + retain_recipe ------------------------------------------------
from kvm_agent.memory.hindsight import _plan_to_text

plan = [{"op": "launch", "app": "cmd"}, {"op": "type", "text": "hi"}, {"op": "sleep", "secs": 1},
        {"op": "click", "target": "Firefox"}, {"op": "verify", "ask": "ok?"}, {"op": "done"}]
txt = _plan_to_text(plan)
check("plan_to_text renders launch", "launch cmd" in txt)
check("plan_to_text renders click target", "click 'Firefox'" in txt)
check("plan_to_text skips sleep/done", "sleep" not in txt and "done" not in txt)

cap2 = {}
mr = HindsightMemory(base_url="http://x", bank="B")
mr._post = lambda path, body: cap2.update(path=path, body=body) or {"success": True, "items_count": 1}
check("retain_recipe returns True", mr.retain_recipe("install firefox", plan) is True)
content = cap2["body"]["items"][0]["content"]
check("retain_recipe stores goal + steps", "install firefox" in content and "launch cmd" in content)
check("retain_recipe tags it 'recipe'", "recipe" in (cap2["body"]["items"][0].get("tags") or []))
mr._post = lambda path, body: {"success": True}
check("retain_recipe empty plan -> False (no post)", mr.retain_recipe("g", []) is False)

# 9. dedup-on-write: skip a recipe that's already stored --------------------------------------
np_plan = [{"op": "launch", "app": "notepad"}, {"op": "type", "text": "hello from memory"},
           {"op": "verify", "ask": "x"}, {"op": "done"}]
md = HindsightMemory(base_url="http://x", bank="B")
posted = {"n": 0}
md._post = lambda p, b: (posted.update(n=posted["n"] + 1), {"success": True})[1]
md.recall = lambda q, **k: ["On this Windows machine, the notepad task typing 'hello from memory' was completed"]
check("dedup: skips a similar existing recipe",
      md.retain_recipe("Open Notepad and type: hello from memory", np_plan) is False)
check("dedup: no write was posted", posted["n"] == 0)
md.recall = lambda q, **k: ["A totally unrelated fact about calculators and arithmetic"]
check("dedup: writes when nothing similar exists",
      md.retain_recipe("Open Notepad and type: hello from memory", np_plan) is True)
check("dedup: the write happened", posted["n"] == 1)

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
