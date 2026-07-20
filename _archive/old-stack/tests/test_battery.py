"""
test_battery.py — OFFLINE tests for kvm_agent.battery.runner (2026-07-17, updated 2026-07-18
for flaw #11: refusal-vs-exhaustion scoring on expect_answer=False tasks).

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
    """`refusal` controls judge_refusal's return (flaw #11); omit/None to simulate a
    verifier that can't judge refusals at all (hasattr fallback -> fail-open)."""
    def __init__(self, any_backend=True, refusal=None):
        self._any = any_backend
        self._refusal = refusal

    def available(self):
        return {"tesseract": self._any, "vision": self._any, "any": self._any}

    def judge_refusal(self, answer_text):
        return self._refusal


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


def _run_result(finished, answer_text=""):
    return {"finished": finished, "answer_text": answer_text}


def run_one(task, finished, run_fn=None, verifier=None):
    return with_tasks([task], lambda: battery_runner.run_battery(
        run_fn=run_fn or (lambda *a, **k: _run_result(finished)),
        capture_fn=lambda: b"PNG",
        verifier=verifier or FakeVerifier(),
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

# 2. expect_answer=False (impossible task), flaw #11: refusal-vs-exhaustion ---------------------
# 2a. exhaustion: never answered at all -> deterministic failure, no judge call needed.
t5 = Task(id="t5", category="impossible", goal="g", expect_answer=False, grade=None)
s = run_one(t5, finished=False, verifier=FakeVerifier(refusal=True))
check("expect_answer=False, finished=False (exhausted budget) -> NOT correct "
      "(no distinct refusal signal was ever given, even though a judge WOULD say refusal)",
      s["results"][0]["correct"] is False)
check("exhaustion is deterministic -- graded stays None, grading 'verified' (no ambiguity)",
      s["results"][0]["graded"] is None and s["results"][0]["grading"] == "verified")

# 2b. answered, judge says genuine refusal ("Photoshop isn't installed") -> correct.
s = run_one(t5, finished=True,
            run_fn=lambda *a, **k: _run_result(True, "Photoshop is not installed on this VM."),
            verifier=FakeVerifier(refusal=True))
check("expect_answer=False, finished=True + judge says genuine refusal -> correct",
      s["results"][0]["correct"] is True)
check("genuine refusal -> grading 'verified'", s["results"][0]["grading"] == "verified")
check("answer_text is captured on the result", "Photoshop" in s["results"][0]["answer_text"])

# 2c. answered, judge says this is a FALSE claim of success -> NOT correct (the original
#     Phase I5 false-positive-finish failure mode, now actually caught).
s = run_one(t5, finished=True,
            run_fn=lambda *a, **k: _run_result(True, "Successfully opened Photoshop and created a new document."),
            verifier=FakeVerifier(refusal=False))
check("expect_answer=False, finished=True + judge says NOT a refusal (false success claim) "
      "-> NOT correct", s["results"][0]["correct"] is False)

# 2d. answered, but the judge backend is unreachable -> fail-open (self-report), flagged
#     unverified -- same contract as the expect_answer=True graded=None case.
s = run_one(t5, finished=True,
            run_fn=lambda *a, **k: _run_result(True, "can't find that app"),
            verifier=FakeVerifier(refusal=None))
check("expect_answer=False, finished=True + judge unreachable -> correct (fail-open)",
      s["results"][0]["correct"] is True)
check("judge unreachable -> grading 'unverified'", s["results"][0]["grading"] == "unverified")

# 3. --only filtering ---------------------------------------------------------------------------
def _test_only_filter():
    s = battery_runner.run_battery(task_ids=["b"], run_fn=lambda *a, **k: _run_result(True),
                                    capture_fn=lambda: b"PNG", verifier=FakeVerifier())
    check("--only filters to the requested task", [r["task_id"] for r in s["results"]] == ["b"])
    try:
        battery_runner.run_battery(task_ids=["nope"], run_fn=lambda *a, **k: _run_result(True),
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
    s = battery_runner.run_battery(run_fn=lambda *a, **k: _run_result(True), capture_fn=lambda: b"PNG",
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
        run_fn=lambda *a, **k: _run_result(finished), capture_fn=lambda: b"PNG", verifier=verifier))

s = run_one_v(Task(id="v", category="core", goal="g", grade=fake_grade_true), True, FakeVerifier(True))
check("grade True -> grading 'verified'", s["results"][0]["grading"] == "verified")
check("verified run: n_unverified 0", s["n_unverified"] == 0)

s = run_one_v(Task(id="u", category="core", goal="g", grade=fake_grade_none), True, FakeVerifier(False))
check("grade None + backend down -> grading 'unverified'", s["results"][0]["grading"] == "unverified")
check("unverified counted in n_unverified", s["n_unverified"] == 1)
check("summary records grading_backends", s["grading_backends"]["any"] is False)
check("unverified still not silently dropped (correct reflects self-report, flagged)",
      s["results"][0]["correct"] is True and s["results"][0]["grading"] == "unverified")

# 5b. reset between tasks (flaw #7): reset_fn called once per task, BEFORE the run ---------------
def _test_reset_between_tasks():
    order = []
    def reset(): order.append("reset")
    def run(*a, **k): order.append("run"); return _run_result(True)
    s = battery_runner.run_battery(run_fn=run, capture_fn=lambda: b"PNG",
                                    verifier=FakeVerifier(), reset_fn=reset)
    check("reset_fn called once per task", order.count("reset") == s["n"])
    check("reset happens BEFORE each run (reset,run,reset,run,...)",
          order == ["reset", "run"] * s["n"])

with_tasks([
    Task(id="r1", category="core", goal="g", grade=fake_grade_true),
    Task(id="r2", category="core", goal="g", grade=fake_grade_true),
], _test_reset_between_tasks)

# 5c. offline/injected run must NOT touch the VM (no reset_fn given, run_fn injected) -------------
def _test_no_vm_in_offline():
    # run_fn+capture_fn injected -> live=False -> reset defaults to a no-op, never imports/uses
    # VMController. If this raised/hung it would mean the offline path reached virsh.
    s = battery_runner.run_battery(run_fn=lambda *a, **k: _run_result(True), capture_fn=lambda: b"PNG",
                                    verifier=FakeVerifier())
    check("offline run completes without VM reset", s["n"] >= 1)

with_tasks([Task(id="o1", category="core", goal="g", grade=fake_grade_true)], _test_no_vm_in_offline)

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
