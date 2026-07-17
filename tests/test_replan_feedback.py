"""
test_replan_feedback.py — OFFLINE unit tests for the 2026-06-21 replan-feedback + reasoning work.

No rig, no Ollama, no API keys: every model call is faked. Exercises exactly the pieces that
changed, so a regression shows up here instead of on a live Firefox run.

    python tests\test_replan_feedback.py     # prints PASS/FAIL per check, exits non-zero on any fail
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.config import Config
from kvm_agent.orchestration.planner import (
    _extract_json, summarize_result, validate_plan, plan_is_actionable, Planner)
from kvm_agent.orchestration.executive import Executive

_FAILS = []


def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)


# ── 1. _extract_json strips a <think> trace (incl. brackets inside it) ───────────────────────
think = '<think>First I click [the button] and pick item [0] from the list.</think>\n[{"op":"done"}]'
check("extract_json: strips <think> with brackets", _extract_json(think) == [{"op": "done"}])
check("extract_json: still handles ```json fences",
      _extract_json('```json\n[{"op":"launch","app":"cmd"}]\n```')[0]["app"] == "cmd")
check("extract_json: plain array unaffected",
      _extract_json('[{"op":"type","text":"hi"}]')[0]["text"] == "hi")
# a split reasoning_content can leave a bare closing tag in front of the plan
check("extract_json: stray </think> close tag",
      _extract_json('reasoning…</think>\n[{"op":"done"}]') == [{"op": "done"}])


# ── 2. summarize_result: prefer failure_summary, handle no-op + verify mismatch ──────────────
check("summarize: failure_summary passthrough",
      summarize_result({"status": "failed@1:launch", "failure_summary": "HELLO WORLD"}) == "HELLO WORLD")
check("summarize: no-op plan message",
      "no real actions" in summarize_result({"status": "no-op plan: ...", "log": []}))
vr = {"status": "failed@3:verify",
      "log": [{"i": 3, "op": "verify", "step": {"op": "verify", "number==": "61"}, "got": "5985"}]}
s = summarize_result(vr)
check("summarize: verify mismatch shows got+expected", "5985" in s and "61" in s)
check("summarize: non-dict safe", isinstance(summarize_result(None), str))


# ── 3. replan: builds a history-aware prompt and parses the model's plan ─────────────────────
class FakePlanner(Planner):
    """Captures the user message replan() sends and returns a canned plan."""
    def __init__(self):
        self.captured = None

    def _complete(self, user_msg, screen_png=None):
        self.captured = user_msg
        return '[{"op":"launch","app":"cmd"},{"op":"done"}]'


fp = FakePlanner()
result = {"status": "failed@1:launch",
          "failure_summary": "step 1 (launch 'firefox') failed: could not confirm 'firefox' opened."}
history = ["step 1 (launch 'firefox') failed: Win+R could not find it",
           "step 1 (launch 'firefox') failed: could not confirm 'firefox' opened."]
plan = fp.replan("install firefox and set default", result, None, history)
check("replan: returns the parsed plan", plan[0]["op"] == "launch" and plan[0]["app"] == "cmd")
check("replan: prompt lists PREVIOUS ATTEMPTS", "PREVIOUS ATTEMPTS" in fp.captured)
check("replan: prompt has MOST RECENT FAILURE", "MOST RECENT FAILURE" in fp.captured)
check("replan: prompt forbids repeating", "do not repeat" in fp.captured.lower())
check("replan: includes the prior attempt text", "Win+R could not find it" in fp.captured)

# with a single-item history there is no PREVIOUS ATTEMPTS block, only the latest
fp2 = FakePlanner()
fp2.replan("g", result, None, ["only one failure so far"])
check("replan: single history -> no PREVIOUS ATTEMPTS block", "PREVIOUS ATTEMPTS" not in fp2.captured)
# empty history falls back to summarizing the result object itself
fp3 = FakePlanner()
fp3.replan("g", result, None, None)
check("replan: empty history falls back to result summary",
      "could not confirm 'firefox' opened" in fp3.captured)


# ── 4. Executive._failure_summary: real diagnosis + on-screen text, no rig ───────────────────
class FakeEnv:
    def observe(self):
        return {"screenshot": b"PNGBYTES"}


class FakeVerifier:
    def __init__(self, text):
        self._text = text

    def read_text(self, png):
        return self._text


class FakeAgent:
    pass


ex = Executive(FakeEnv(), FakeAgent(),
               verifier=FakeVerifier("Default apps  Web browser  Google Chrome  Microsoft Edge"),
               capture=False)
click_rec = {"i": 5, "op": "click", "step": {"op": "click", "target": "Firefox"},
             "ground": {"xy": (470, 910)}, "ok": False}
cs = ex._failure_summary(click_rec)
check("failure_summary: click names op+target", "click" in cs and "Firefox" in cs)
check("failure_summary: click reports the coordinate", "470" in cs and "910" in cs)
check("failure_summary: includes on-screen text", "Google Chrome" in cs)

verify_rec = {"i": 3, "op": "verify", "step": {"op": "verify", "number==": "61"},
              "got": "5985", "ok": False}
vs = ex._failure_summary(verify_rec)
check("failure_summary: verify reports read-vs-expected", "5985" in vs and "61" in vs)

launch_rec = {"i": 0, "op": "launch", "step": {"op": "launch", "app": "firefox"}, "ok": False}
check("failure_summary: launch explains confirm failure",
      "could not confirm" in ex._failure_summary(launch_rec))


# ── 5. CFG.planner_effective_max_tokens: auto vs explicit vs name-based ───────────────────────
check("max_tokens: non-thinking auto = 4000",
      Config(planner_thinking=False, planner_max_tokens=0,
             planner_model="Qwen/Qwen3-VL-8B-Instruct").planner_effective_max_tokens == 4000)
check("max_tokens: thinking flag auto = 16000",
      Config(planner_thinking=True, planner_max_tokens=0,
             planner_model="Qwen/Qwen3-VL-8B-Instruct").planner_effective_max_tokens == 16000)
check("max_tokens: 'Thinking' in model name auto = 16000",
      Config(planner_thinking=False, planner_max_tokens=0,
             planner_model="Qwen/Qwen3-VL-8B-Thinking").planner_effective_max_tokens == 16000)
check("max_tokens: explicit override wins",
      Config(planner_thinking=True, planner_max_tokens=9000).planner_effective_max_tokens == 9000)


# ── 6. sanity: the unchanged lint/actionable helpers still behave ────────────────────────────
clean, issues = validate_plan([{"op": "launch", "app": "cmd"},
                               {"op": "verify", "expect": "Google Chrome is now the default browser"}])
check("validate_plan: claim-like expect -> ask conversion",
      any(s.get("op") == "verify" and "ask" in s for s in clean))
check("plan_is_actionable: launch is actionable",
      plan_is_actionable([{"op": "launch", "app": "cmd"}]) is True)
check("plan_is_actionable: done/verify only is NOT actionable",
      plan_is_actionable([{"op": "verify", "expect": "x"}, {"op": "done"}]) is False)


print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
