#!/usr/bin/env python3
"""
battery.py — human-graded task battery for the physical target
(docs/PLAN_2026-07-20_physical_target_move.md §5).

Per task: operator reboots the laptop (target.reboot) -> the Holo loop runs with full
RunRecorder instrumentation -> the operator grades pass/fail from the final frame +
run artifacts. NO automated grading at this stage: the user is the grader, and no
None/uncertain grade can ever masquerade as a pass (finding #8 — fail-open grading is
the anti-pattern this project exists to kill).

    python tools/battery.py tools/battery_tasks_gnome.json

Artifacts (AGENTS.md §1 — everything under runs/):
    runs/battery_<task_id>_<ts>/    per-task RunRecorder dirs (written by run())
    runs/battery_<ts>/results.json  grades + provenance for the whole battery
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.config import CFG
from kvm_agent.hardware import target
from kvm_agent.models.holo import HoloVerifier
import agent_loop_holo
from agent_loop_holo import boot, run, shutdown

VERIFY_MODES = ("off", "shadow")  # "gate" is roadmap Phase 2 slice D-c, not built yet


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
    silently recorded (finding #8). Input form: 'p <optional note>' / 'f <optional note>' /
    'v <REQUIRED note>'. 'v' (void) means the task was infeasible on this target and
    is excluded from the score's denominator — 2026-07-22: paint_line had no paint app
    installed on the GNOME target and the p/f-only vocabulary forced the operator to
    record a protest "pass" (finding #8's fail-open class, in the grade vocabulary)."""
    # Show the model's own verdict + where the evidence lives before asking for a grade.
    print(f"[battery] model verdict: finished={result['finished']} "
          f"answer_text={result['answer_text']!r}")
    print(f"[battery] evidence in runs/battery_{task['id']}_<ts>/ (step frames, raw outputs)")
    while True:
        raw = input(f"[battery] task {task['id']!r}: grade [p/f/v] + note (v REQUIRES one): ").strip()
        grade = {"p": "pass", "f": "fail", "v": "void"}.get(raw[:1])
        note = raw[1:].strip()
        if grade == "void" and not note:
            print("[battery] a void grade REQUIRES a note saying why — no silent exclusions")
            continue
        if grade:
            return {"grade": grade, "note": note}
        print("[battery] need 'p', 'f' or 'v' — no grade, no continue")


def auto_grade_from_verdict(verified_finish):
    """Map a run()'s `verified_finish` (a Verdict.to_dict(), or None) into the SAME
    pass/fail vocabulary grade_task uses -- roadmap Phase 2 slice D-b: the oracle's
    verdict travels ALONGSIDE the human grade (never replacing it; `grader` stays
    "human" until slice D-c), so agreement between the two is computable.

    Fail-closed, matching grade_task's own p/f/v discipline: satisfied=None (the
    oracle didn't answer) is NOT a silent pass -- it maps to (None, evidence), not
    ("pass", ...). (None, None) when there is no verdict at all (verify_mode="off", or
    the task never reached a finished claim)."""
    if verified_finish is None:
        return None, None
    grade = {True: "pass", False: "fail"}.get(verified_finish.get("satisfied"))
    return grade, verified_finish.get("evidence")


def write_results(path, payload):
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"[battery] results -> {path}")


def make_payload(ts, tasks_path, psr_active, tasks, results):
    """Fail-closed scoring (2026-07-21 second review #8): the denominator is ALL
    tasks, not just the graded ones -- an abandoned battery previously reported
    '1/1', indistinguishable from a finished one (finding #8's fail-open class,
    one level up). Void grades (infeasible tasks) leave the denominator but stay
    visible in the score string -- a void can never inflate the pass count."""
    passes = sum(r["grade"] == "pass" for r in results)
    voids = sum(r["grade"] == "void" for r in results)
    score = f"{passes}/{len(tasks) - voids}" + (f" ({voids} void)" if voids else "")
    return {"started": ts, "tasks_file": tasks_path, "psr_active": psr_active,
            "total_tasks": len(tasks), "graded": len(results),
            "complete": len(results) == len(tasks),
            "results": results,
            "score": score}


def main():
    # No default task file (2026-07-22): the old default was the WINDOWS shakedown
    # list, a wrong-OS trap on the GNOME target -- name the target's list explicitly.
    if len(sys.argv) < 2:
        sys.exit("usage: python tools/battery.py <task_file.json> [verify_mode]\n"
                 "  e.g. tools/battery_tasks_gnome.json (GNOME target)\n"
                 "       tools/battery_tasks_shakedown.json (Windows target)\n"
                 "  verify_mode: off (default) | shadow -- roadmap Phase 2 slice D-b\n"
                 "    (docs/PLAN_2026-07-22_phase2_subgoal_verification.md). 'shadow'\n"
                 "    records the postcondition oracle's verdict on every task's own\n"
                 "    finished claim ALONGSIDE the human grade; grading itself is\n"
                 "    unchanged -- 'grader' stays \"human\" until slice D-c.")
    tasks_path = sys.argv[1]
    verify_mode = sys.argv[2] if len(sys.argv) > 2 else "off"
    if verify_mode not in VERIFY_MODES:
        sys.exit(f"verify_mode must be one of {VERIFY_MODES} (D-c's 'gate' isn't "
                 f"built yet), got {verify_mode!r}")
    # Constructed once for the whole battery, not per task: HoloVerifier is stateless
    # (kvm_agent.models.base.Verifier's contract), so nothing about re-task-ing it is
    # unsafe, and it avoids re-paying object construction 5-6 times per battery.
    verifier = HoloVerifier() if verify_mode == "shadow" else None
    tasks = load_tasks(tasks_path)
    ts = time.strftime("%Y%m%d_%H%M%S")
    print(f"[battery] {len(tasks)} tasks from {tasks_path}")
    if CFG.target_shell == "windows":
        # Steps Recorder (psr.exe) is the independent ground-truth channel on a
        # Windows target (what Windows actually received vs what the capture card
        # saw). It does not exist on the Ubuntu/GNOME target.
        print("[battery] REMINDER: start Steps Recorder (psr.exe) on the laptop (raise its "
              "100-capture cap in its settings first) and drop its .zip into the battery's "
              "run dirs afterward — it is the independent ground-truth channel.")
        raw = input("[battery] Steps Recorder active on the laptop? [y/n]: ")
        psr_active = raw.strip().lower().startswith("y")
    else:
        print(f"[battery] target shell is {CFG.target_shell!r} — no psr.exe ground-truth "
              "channel (Windows-only); the camera is the only evidence channel.")
        psr_active = False
    # One folder per battery (AGENTS.md §1): the summary was previously a loose file
    # at the runs/ root while every other artifact is foldered (2026-07-21 review).
    results_dir = os.path.join(CFG.runs_dir, f"battery_{ts}")
    os.makedirs(results_dir, exist_ok=True)
    results_path = os.path.join(results_dir, "results.json")

    def payload():
        return make_payload(ts, tasks_path, psr_active, tasks, results)

    # verify=False: the battery runs its OWN HID gate per task, post-reboot, with an
    # interactive replug loop (below) -- a boot-time gate raise here would kill the
    # whole battery non-interactively instead.
    boot(verify=False)
    results = []
    try:
        for i, task in enumerate(tasks):
            print(f"\n[battery] === task {i + 1}/{len(tasks)}: {task['id']} ===")
            if task.get("setup"):
                print(f"[battery] setup: {task['setup']}")
            target.reboot()
            # Post-reboot HID gate (2026-07-21): a physical reboot can bring the
            # composite HID device up half-dead (keyboard alive, mouse dead, probe
            # flags LYING) -- camera-verified round-trips are the only truth. The
            # operator fixes it by replugging the Pico's USB at the laptop.
            while True:
                hid_ok, detail = target.verify_hid(agent_loop_holo.ENV.r4,
                                                 agent_loop_holo.ENV.cam,
                                                 screen=CFG.screen_size)
                print(f"[battery] hid gate: {detail}")
                if hid_ok:
                    break
                input("[battery] HID not delivering -- replug the Pico's USB at the "
                      "laptop (or power-cycle it), then press Enter to re-test... ")
            tag = f"battery_{task['id']}"
            # no_progress_abort=False per H1 (2026-07-19): the frozen-screen/same-click
            # aborts fired falsely on recoverable tasks; benchmark runs give the full budget.
            result = run(task["instruction"], max_steps=task["max_steps"],
                         confirm_first=0, tag=tag, no_progress_abort=False,
                         verify_mode=verify_mode, verifier=verifier)
            verdict = grade_task(task, result)
            auto_grade, auto_evidence = auto_grade_from_verdict(result.get("verified_finish"))
            if auto_grade is not None:
                print(f"[battery] {task['id']}: oracle says {auto_grade!r} "
                      f"({(auto_evidence or '')[:100]})")
            results.append({"task_id": task["id"], "instruction": task["instruction"],
                            "run_tag": tag, "finished": result["finished"],
                            "answer_text": result["answer_text"], "grader": "human",
                            "auto_grade": auto_grade, "auto_evidence": auto_evidence,
                            **verdict})
            print(f"[battery] {task['id']}: {verdict['grade']} ({verdict['note']})")
            # Incremental write: a crash mid-battery must not lose grades already taken.
            write_results(results_path, payload())
    finally:
        shutdown()  # releases the camera even if a task raised mid-battery
    write_results(results_path, payload())


if __name__ == "__main__":
    main()
