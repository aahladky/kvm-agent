#!/usr/bin/env python3
"""
battery.py — human-graded task battery for the physical target
(docs/PLAN_2026-07-20_physical_target_move.md §5).

Per task: operator reboots the laptop (target.reboot) -> the Holo loop runs with full
RunRecorder instrumentation -> the postcondition verifier grades pass/fail. Human
grading remains available via --human and is required for verifier/model disagreement
plus a random sample of agreements. No None/uncertain verdict can masquerade as a pass
(finding #8 — fail-open grading is the anti-pattern this project exists to kill).

    python tools/battery.py tools/battery_tasks_gnome.json

Artifacts (AGENTS.md §1 — everything under runs/):
    runs/battery_<task_id>_<ts>/    per-task RunRecorder dirs (written by run())
    runs/battery_<ts>/results.json  grades + provenance for the whole battery
"""
import argparse
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.config import CFG
from kvm_agent.hardware import target
from kvm_agent.hardware.env import png_to_model_input_jpeg
from kvm_agent.models.holo import HoloVerifier, jpeg_bytes_to_data_url
import agent_loop_holo
from agent_loop_holo import boot, run, shutdown

VERIFY_MODES = ("off", "shadow", "gate")
RESET_STRATEGIES = ("manual-power-cycle", "cleanup", "none")
RESET_QUESTION = (
    "Is this a clean GNOME desktop ready for a new test? Answer true only if there is "
    "no terminal window (especially no KVM_RESET_FAILED), no Text Editor, Calculator, "
    "Settings, Files, or Pinta task window open, and this is not a login or lock screen. "
    "The normal GNOME top bar, dock, desktop background, and desktop icons are allowed.")


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
        reset = t.setdefault("reset", {})
        assert isinstance(reset, dict), f"task {t['id']!r} reset must be an object"
        reset.setdefault("cleanup_files", [])
        reset.setdefault("setting_resets", [])
        reset.setdefault("application_reset", "battery-apps")
        target.validate_reset_manifest(reset["cleanup_files"], reset["setting_resets"],
                                       reset["application_reset"])
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


def verifier_grade(verified_finish):
    """D-c's primary grader. Fail closed on a false, unanswered, or missing verdict."""
    grade, evidence = auto_grade_from_verdict(verified_finish)
    if grade == "pass":
        return {"grade": "pass", "note": evidence or "verifier accepted postcondition"}
    if grade == "fail":
        return {"grade": "fail", "note": evidence or "verifier rejected postcondition"}
    return {"grade": "fail",
            "note": evidence or "no verifier verdict: task never reached an accepted "
                                "finished claim"}


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Run the physical-target battery with fail-closed verification.")
    ap.add_argument("task_file")
    ap.add_argument("verify_mode", nargs="?", choices=VERIFY_MODES, default="gate",
                    help="off, shadow, or gate (default: gate)")
    ap.add_argument("--human", action="store_true",
                    help="human grades every task instead of verifier-primary grading")
    ap.add_argument("--spot-check-pct", type=float, default=None,
                    help="human-grade this random percentage of verifier agreements "
                         "(default: 10)")
    ap.add_argument("--no-reboot", action="store_true",
                    help=argparse.SUPPRESS)  # compatibility alias for reset=none
    ap.add_argument("--reset-strategy", choices=RESET_STRATEGIES,
                    default="manual-power-cycle",
                    help="between-task reset (default: manual-power-cycle)")
    args = ap.parse_args(argv)
    if args.no_reboot:
        if args.reset_strategy != "manual-power-cycle":
            ap.error("--no-reboot cannot be combined with --reset-strategy")
        args.reset_strategy = "none"
    if args.spot_check_pct is None:
        args.spot_check_pct = 0.0 if args.reset_strategy == "none" else 10.0
    if not 0 <= args.spot_check_pct <= 100:
        ap.error("--spot-check-pct must be between 0 and 100")
    if args.verify_mode == "off" and not args.human:
        ap.error("verifier-primary grading requires verify_mode shadow or gate; "
                 "use --human with off")
    if args.reset_strategy == "none" and args.human:
        ap.error("--no-reboot is the no-human unattended mode and cannot use --human")
    return args


def write_results(path, payload):
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"[battery] results -> {path}")


def make_payload(ts, tasks_path, psr_active, tasks, results, run_config=None,
                 reset_events=None):
    """Fail-closed scoring (2026-07-21 second review #8): the denominator is ALL
    tasks, not just the graded ones -- an abandoned battery previously reported
    '1/1', indistinguishable from a finished one (finding #8's fail-open class,
    one level up). Void grades (infeasible tasks) leave the denominator but stay
    visible in the score string -- a void can never inflate the pass count."""
    passes = sum(r["grade"] == "pass" for r in results)
    voids = sum(r["grade"] == "void" for r in results)
    score = f"{passes}/{len(tasks) - voids}" + (f" ({voids} void)" if voids else "")
    return {"started": ts, "tasks_file": tasks_path, "psr_active": psr_active,
            "run_config": run_config or {},
            "reset_events": reset_events or [],
            "total_tasks": len(tasks), "graded": len(results),
            "complete": len(results) == len(tasks),
            "results": results,
            "score": score}


def main():
    args = parse_args()
    tasks_path = args.task_file
    verify_mode = args.verify_mode
    run_config = {
        "verify_mode": verify_mode,
        "grader": "human" if args.human else "verifier",
        "spot_check_pct": args.spot_check_pct,
        "reset_strategy": args.reset_strategy,
    }
    # Constructed once for the whole battery, not per task: HoloVerifier is stateless
    # (kvm_agent.models.base.Verifier's contract), so nothing about re-task-ing it is
    # unsafe, and it avoids re-paying object construction 5-6 times per battery.
    verifier = HoloVerifier() if verify_mode != "off" else None
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
    reset_events = []

    def payload():
        return make_payload(ts, tasks_path, psr_active, tasks, results, run_config,
                            reset_events)

    # verify=False: the battery runs its OWN HID gate per task, post-reboot, with an
    # interactive replug loop (below) -- a boot-time gate raise here would kill the
    # whole battery non-interactively instead.
    # --no-reboot must contain no hidden input(): use boot's one-shot, fail-closed HID
    # gate and then carry state. Normal mode retains the post-reboot interactive replug
    # loop because a reboot can bring up a half-dead composite device.
    boot(verify=args.reset_strategy == "none")
    results = []
    try:
        for i, task in enumerate(tasks):
            print(f"\n[battery] === task {i + 1}/{len(tasks)}: {task['id']} ===")
            if task.get("setup"):
                print(f"[battery] setup: {task['setup']}")
            reset = task["reset"]
            if args.reset_strategy == "manual-power-cycle":
                target.reboot()
            elif args.reset_strategy == "cleanup":
                command = target.reset_gnome_session(
                    agent_loop_holo.ENV.r4,
                    cleanup_files=reset["cleanup_files"],
                    setting_resets=reset["setting_resets"],
                    application_reset=reset["application_reset"])
                print(f"[battery] reset command sent (cleanup): {command}")
                png = agent_loop_holo.ENV.cam.png_bytes()
                reset_url = jpeg_bytes_to_data_url(
                    png_to_model_input_jpeg(png, CFG.holo_model_input_res))
                reset_verifier = verifier or HoloVerifier()
                reset_verdict = reset_verifier.check(
                    reset_url, agent_loop_holo.ENV.screen_width,
                    agent_loop_holo.ENV.screen_height, RESET_QUESTION)
                event = {"task_id": task["id"], **reset_verdict.to_dict()}
                reset_events.append(event)
                write_results(results_path, payload())
                print(f"[battery] reset verify: satisfied={reset_verdict.satisfied} "
                      f"({reset_verdict.evidence[:120]})")
                if reset_verdict.satisfied is not True:
                    raise RuntimeError(
                        f"desktop reset not verified for {task['id']}: "
                        f"{reset_verdict.evidence}")
            # Post-reboot HID gate (2026-07-21): a physical reboot can bring the
            # composite HID device up half-dead (keyboard alive, mouse dead, probe
            # flags LYING) -- camera-verified round-trips are the only truth. The
            # operator fixes it by replugging the Pico's USB at the laptop.
            if args.reset_strategy != "none":
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
            auto_grade, auto_evidence = auto_grade_from_verdict(result.get("verified_finish"))
            if auto_grade is not None:
                print(f"[battery] {task['id']}: oracle says {auto_grade!r} "
                      f"({(auto_evidence or '')[:100]})")
            final_verdict = result.get("verified_finish")
            disagreement = bool(result.get("verification_refusals")) or (
                result.get("finished") and final_verdict is not None
                and final_verdict.get("satisfied") is not True)
            sampled = random.random() * 100 < args.spot_check_pct
            needs_human = args.reset_strategy != "none" and (
                args.human or disagreement or sampled)
            human_verdict = grade_task(task, result) if needs_human else None
            if args.human:
                verdict = human_verdict
                grader = "human"
            else:
                verdict = verifier_grade(result.get("verified_finish"))
                grader = "verifier"
                # Infeasibility is not a screen judgement. A human spot-check may be the
                # only authority that can void a task, so preserve that decision.
                if human_verdict and human_verdict["grade"] == "void":
                    verdict = human_verdict
                    grader = "human-void"
            results.append({"task_id": task["id"], "instruction": task["instruction"],
                            "run_tag": tag, "finished": result["finished"],
                            "answer_text": result["answer_text"], "grader": grader,
                            "auto_grade": auto_grade, "auto_evidence": auto_evidence,
                            "human_grade": (human_verdict or {}).get("grade"),
                            "human_note": (human_verdict or {}).get("note"),
                            "spot_check_reason": (
                                "deferred-disagreement-no-reboot"
                                if args.reset_strategy == "none" and disagreement else
                                "all-human" if args.human else
                                "model-verifier-disagreement" if disagreement else
                                "random-agreement-sample" if sampled else None),
                            **verdict})
            print(f"[battery] {task['id']}: {verdict['grade']} ({verdict['note']})")
            # Incremental write: a crash mid-battery must not lose grades already taken.
            write_results(results_path, payload())
    finally:
        shutdown()  # releases the camera even if a task raised mid-battery
    write_results(results_path, payload())


if __name__ == "__main__":
    main()
