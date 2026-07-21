#!/usr/bin/env python3
"""
battery.py — human-graded task battery for the physical target
(docs/PLAN_2026-07-20_physical_target_move.md §5).

Per task: operator reboots the laptop (target.reboot) -> the Holo loop runs with full
RunRecorder instrumentation -> the operator grades pass/fail from the final frame +
run artifacts. NO automated grading at this stage: the user is the grader, and no
None/uncertain grade can ever masquerade as a pass (finding #8 — fail-open grading is
the anti-pattern this project exists to kill).

    python tools/battery.py tools/battery_tasks_shakedown.json

Artifacts (AGENTS.md §1 — everything under runs/):
    runs/battery_<task_id>_<ts>/    per-task RunRecorder dirs (written by run())
    runs/battery_<ts>_results.json  grades + provenance for the whole battery
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.config import CFG
from kvm_agent.hardware import target
from agent_loop_holo import boot, run, shutdown


def load_tasks(path):
    """Read + validate the task list. Each task: {"id", "instruction",
    "max_steps" (optional, default 15), "setup" (optional operator note)}."""
    with open(path) as f:
        tasks = json.load(f)
    assert isinstance(tasks, list) and tasks, "task file must be a non-empty JSON list"
    for t in tasks:
        assert isinstance(t.get("id"), str) and t["id"], f"task missing id: {t!r}"
        assert isinstance(t.get("instruction"), str) and t["instruction"], \
            f"task {t.get('id')!r} missing instruction"
        t.setdefault("max_steps", 15)
    return tasks


def grade_task(task, result):
    """The human grader. No default and no empty answer — a grade can never be
    silently recorded (finding #8). Input form: 'p <optional note>' / 'f <optional note>'."""
    while True:
        raw = input(f"[battery] task {task['id']!r}: grade [p/f] + optional note: ").strip()
        if raw[:1] in ("p", "f"):
            return {"grade": "pass" if raw[0] == "p" else "fail", "note": raw[1:].strip()}
        print("[battery] need 'p' or 'f' — no grade, no continue")


def write_results(path, payload):
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"[battery] results -> {path}")


def main():
    tasks_path = sys.argv[1] if len(sys.argv) > 1 else "tools/battery_tasks_shakedown.json"
    tasks = load_tasks(tasks_path)
    ts = time.strftime("%Y%m%d_%H%M%S")
    print(f"[battery] {len(tasks)} tasks from {tasks_path}")
    print("[battery] REMINDER: start Steps Recorder (psr.exe) on the laptop (raise its "
          "100-capture cap in its settings first) and drop its .zip into the battery's "
          "run dirs afterward — it is the independent ground-truth channel.")
    boot()
    results = []
    for i, task in enumerate(tasks):
        print(f"\n[battery] === task {i + 1}/{len(tasks)}: {task['id']} ===")
        if task.get("setup"):
            print(f"[battery] setup: {task['setup']}")
        target.reboot()
        tag = f"battery_{task['id']}"
        # no_progress_abort=False per H1 (2026-07-19): the frozen-screen/same-click
        # aborts fired falsely on recoverable tasks; benchmark runs give the full budget.
        result = run(task["instruction"], max_steps=task["max_steps"],
                     confirm_first=0, tag=tag, no_progress_abort=False)
        verdict = grade_task(task, result)
        results.append({"task_id": task["id"], "instruction": task["instruction"],
                        "run_tag": tag, "finished": result["finished"],
                        "answer_text": result["answer_text"], "grader": "human", **verdict})
        print(f"[battery] {task['id']}: {verdict['grade']} ({verdict['note']})")
    shutdown()
    payload = {"started": ts, "tasks_file": tasks_path, "results": results,
               "score": f"{sum(r['grade'] == 'pass' for r in results)}/{len(results)}"}
    write_results(os.path.join(CFG.runs_dir, f"battery_{ts}_results.json"), payload)


if __name__ == "__main__":
    main()
