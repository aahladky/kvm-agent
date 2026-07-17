"""Battery runner: drives kvm_agent.battery.tasks.TASKS through the live rig, grades
each task's FINAL frame independently of the model's self-report, and writes an
aggregate battery_summary.json alongside the per-task RunRecorder logs that
agent_loop_holo.run() already produces.

    python -m kvm_agent.battery.runner                 # full battery
    python -m kvm_agent.battery.runner --only calc_basic notepad_type
    python -m kvm_agent.battery.runner --confirm-first 3   # gate the first 3 steps of EACH task

Needs the live rig (camera + Pico + a reachable Holo endpoint) -- this module only
runs end-to-end there. Its control flow (grading + aggregation, independent of hardware)
is covered offline by tests/test_battery.py via injected run_fn/capture_fn/verifier.
"""
import argparse
import json
import os
import time

from kvm_agent.battery.tasks import TASKS
from kvm_agent.config import CFG
from kvm_agent.orchestration.executive import Verifier


def _live_backend():
    import agent_loop_holo as loop
    loop.boot()
    return loop.run, loop._frame_png, loop.shutdown


def run_battery(task_ids=None, confirm_first=0, run_fn=None, capture_fn=None, verifier=None):
    """Run TASKS (or the --only subset), grade each with `verifier`, and return/persist
    the aggregate summary. run_fn/capture_fn/verifier default to the live rig via
    agent_loop_holo but are injectable for offline testing.

    Releases the camera/Pico (via agent_loop_holo.shutdown()) when done, but only if this
    call opened them itself -- injected run_fn/capture_fn (tests) own their own lifecycle."""
    shutdown_fn = None
    if run_fn is None or capture_fn is None:
        live_run, live_capture, live_shutdown = _live_backend()
        run_fn = run_fn or live_run
        capture_fn = capture_fn or live_capture
        shutdown_fn = live_shutdown
    verifier = verifier or Verifier()

    tasks = [t for t in TASKS if task_ids is None or t.id in task_ids]
    if not tasks:
        raise ValueError(f"no tasks match {task_ids!r} (known ids: {[t.id for t in TASKS]})")

    batch_tag = time.strftime("battery_%Y%m%d_%H%M%S")
    out_dir = os.path.join(CFG.runs_dir, batch_tag)
    os.makedirs(out_dir, exist_ok=True)

    results = []
    try:
        for task in tasks:
            print(f"\n=== {task.id} ({task.category}) ===\n{task.goal}")
            finished = run_fn(task.goal, max_steps=task.max_steps, confirm_first=confirm_first,
                               record=True, tag=f"{batch_tag}__{task.id}")
            final_png = capture_fn()
            graded = task.grade(final_png, verifier) if task.grade else None

            if task.expect_answer:
                # correct requires BOTH the model's own completion signal AND an independent
                # screen check that didn't come back a hard "no" (None = "can't verify", not a
                # failure -- see Verifier's own None-means-unknown convention)
                correct = bool(finished) and (graded is not False)
            else:
                # impossible task: correct = did NOT falsely call `answer` (the Phase I5 failure mode)
                correct = not finished

            result = {
                "task_id": task.id, "category": task.category, "goal": task.goal,
                "finished_signal": bool(finished), "graded": graded, "correct": correct,
            }
            print(f"--> finished={finished} graded={graded} correct={correct}")
            results.append(result)
    finally:
        if shutdown_fn:
            shutdown_fn()

    by_category = {}
    for r in results:
        c = by_category.setdefault(r["category"], {"n": 0, "correct": 0})
        c["n"] += 1
        c["correct"] += int(r["correct"])

    summary = {
        "batch": batch_tag,
        "n": len(results),
        "correct": sum(r["correct"] for r in results),
        "by_category": by_category,
        "results": results,
    }
    with open(os.path.join(out_dir, "battery_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n{summary['correct']}/{summary['n']} correct -> {out_dir}/battery_summary.json")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", help="task ids to run (default: all)")
    ap.add_argument("--confirm-first", type=int, default=0,
                     help="gate the first N steps of EACH task with a keypress preview (default: unattended)")
    args = ap.parse_args()
    run_battery(task_ids=args.only, confirm_first=args.confirm_first)
