"""
test_battery.py — OFFLINE tests for kvm_agent.battery.runner (2026-07-17).

No rig: run_fn/capture_fn/verifier are faked and injected into run_battery(), so this
covers the grading/aggregation control flow (expect_answer correct-logic, category
rollup, summary persistence) without touching hardware or a real task list.

    python tests\test_battery.py
"""
import json
import shutil
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.battery.tasks import Task
from kvm_agent.battery import runner as battery_runner

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)


class FakeVerifier:
    def __init__(self, any_backend=True):
        self._any = any_backend

    def available(self):
        return {"tesseract": self._any, "vision": self._any, "any": self._any}


def fake_grade_true(png, verifier):
    return True


def fake_grade_false(png, verifier):
    return False


def fake_grade_none(png, verifier):
    return None


def with_tasks(tasks, fn):
    """Run fn() with battery_runner.TASKS (the name run_battery() actually reads,
    bound via `from kvm_agent.battery.tasks import TASKS`) swapped for `tasks`."""
    saved = battery_runner.TASKS
    battery_runner.TASKS = tasks
    try:
        return fn()
    finally:
        battery_runner.TASKS = saved


def run_one(task, finished, run_fn=None):
    return with_tasks([task], lambda: battery_runner.run_battery(
        run_fn=run_fn or (lambda *a, **k: finished),
        capture_fn=lambda: b"PNG",
        verifier=FakeVerifier(),
    ))


# 1. expect_answer=True: finished + graded-not-False -> correct -------------------------------
s = run_one(Task(id="t1", category="core", goal="g", grade=fake_grade_true), finished=True)
check("expect_answer=True, finished=True, graded=True -> correct", s["results"][0]["correct"] is True)

s = run_one(Task(id="t2", category="core", goal="g", grade=fake_grade_false), finished=True)
check("expect_answer=True, finished=True, graded=False -> NOT correct", s["results"][0]["correct"] is False)

s = run_one(Task(id="t3", category="core", goal="g", grade=fake_grade_none), finished=True)
check("expect_answer=True, finished=True, graded=None (unknown) -> correct (fail-open)",
      s["results"][0]["correct"] is True)

s = run_one(Task(id="t4", category="core", goal="g", grade=fake_grade_true), finished=False)
check("expect_answer=True, finished=False -> NOT correct even if graded True",
      s["results"][0]["correct"] is False)

# 2. expect_answer=False (impossible task): correct == did NOT finish -------------------------
s = run_one(Task(id="t5", category="impossible", goal="g", expect_answer=False, grade=None), finished=False)
check("expect_answer=False, finished=False (honest refusal) -> correct", s["results"][0]["correct"] is True)

s = run_one(Task(id="t6", category="impossible", goal="g", expect_answer=False, grade=None), finished=True)
check("expect_answer=False, finished=True (false-positive finish) -> NOT correct",
      s["results"][0]["correct"] is False)

# 3. --only filtering ---------------------------------------------------------------------------
def _test_only_filter():
    s = battery_runner.run_battery(task_ids=["b"], run_fn=lambda *a, **k: True,
                                    capture_fn=lambda: b"PNG", verifier=FakeVerifier())
    check("--only filters to the requested task", [r["task_id"] for r in s["results"]] == ["b"])
    try:
        battery_runner.run_battery(task_ids=["nope"], run_fn=lambda *a, **k: True,
                                    capture_fn=lambda: b"PNG", verifier=FakeVerifier())
        check("unknown task id raises", False)
    except ValueError:
        check("unknown task id raises", True)

with_tasks([
    Task(id="a", category="core", goal="g", grade=fake_grade_true),
    Task(id="b", category="core", goal="g", grade=fake_grade_true),
], _test_only_filter)

# 4. category rollup + summary file actually written --------------------------------------------
def _test_rollup_and_persistence():
    s = battery_runner.run_battery(run_fn=lambda *a, **k: True, capture_fn=lambda: b"PNG",
                                    verifier=FakeVerifier())
    check("overall correct count", s["correct"] == 2)
    check("by_category rollup: core 1/2", s["by_category"]["core"] == {"n": 2, "correct": 1})
    check("by_category rollup: small_target 1/1", s["by_category"]["small_target"] == {"n": 1, "correct": 1})

    summary_path = os.path.join(battery_runner.CFG.runs_dir, s["batch"], "battery_summary.json")
    check("summary.json written to disk", os.path.exists(summary_path))
    with open(summary_path) as f:
        on_disk = json.load(f)
    check("summary.json round-trips", on_disk["correct"] == s["correct"])
    shutil.rmtree(os.path.join(battery_runner.CFG.runs_dir, s["batch"]))

with_tasks([
    Task(id="c1", category="core", goal="g", grade=fake_grade_true),
    Task(id="c2", category="core", goal="g", grade=fake_grade_false),
    Task(id="s1", category="small_target", goal="g", grade=fake_grade_true),
], _test_rollup_and_persistence)

# 5. grading status (flaw #8): verified / unverified / n/a, and n_unverified surfaced -----------
def run_one_v(task, finished, verifier):
    return with_tasks([task], lambda: battery_runner.run_battery(
        run_fn=lambda *a, **k: finished, capture_fn=lambda: b"PNG", verifier=verifier))

s = run_one_v(Task(id="v", category="core", goal="g", grade=fake_grade_true), True, FakeVerifier(True))
check("grade True -> grading 'verified'", s["results"][0]["grading"] == "verified")
check("verified run: n_unverified 0", s["n_unverified"] == 0)

s = run_one_v(Task(id="u", category="core", goal="g", grade=fake_grade_none), True, FakeVerifier(False))
check("grade None + backend down -> grading 'unverified'", s["results"][0]["grading"] == "unverified")
check("unverified counted in n_unverified", s["n_unverified"] == 1)
check("summary records grading_backends", s["grading_backends"]["any"] is False)
check("unverified still not silently dropped (correct reflects self-report, flagged)",
      s["results"][0]["correct"] is True and s["results"][0]["grading"] == "unverified")

s = run_one_v(Task(id="na", category="impossible", goal="g", expect_answer=False, grade=None), False,
              FakeVerifier(True))
check("no grader by design -> grading 'n/a'", s["results"][0]["grading"] == "n/a")

# 6. real TASKS list sanity (not faked) -----------------------------------------------------------
ids = [t.id for t in battery_runner.TASKS]
check("TASKS has no duplicate ids", len(ids) == len(set(ids)))
check("TASKS covers all five custom categories + core",
      {"core", "scroll_drag", "long_horizon", "wait", "impossible", "small_target"}
      <= set(t.category for t in battery_runner.TASKS))
check("every expect_answer=True task has a grader",
      all(t.grade is not None for t in battery_runner.TASKS if t.expect_answer))

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
