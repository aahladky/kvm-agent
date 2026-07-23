#!/usr/bin/env python3
"""
battery_metrics.py — aggregate a graded battery's results.json + each task's
RunRecorder summary.json/step_NN.json into the numbers roadmap §5 names as tracked
metrics (docs/ROADMAP.md), none of which were computed anywhere before this tool
(roadmap Phase 2 slice D-b, docs/PLAN_2026-07-22_phase2_subgoal_verification.md).

    python tools/battery_metrics.py                  # the latest battery_<ts>/
    python tools/battery_metrics.py battery_20260723  # a specific one (prefix match)
    python tools/battery_metrics.py --all             # every battery ever run, merged

Computes, per battery (and merged across all with --all):
  - completion rate (reuses the battery's own pass/void-aware score string)
  - steps-to-completion distribution (finished vs aborted runs, separately)
  - false-"finished" rate: model claimed done (finished=True) but the human graded it
    fail -- confident-wrong progress the OLD flat loop accepted uncontested. THIS is
    the number roadmap Phase 2's gate names ("false-'finished' rate down").
  - verifier agreement / false-refusal / false-confirmation, IF the battery ran with
    verify_mode="shadow" (tools/battery.py's `auto_grade` column) -- false-refusal is
    the live number that gates slice D-c (an oracle that rejects a true pass);
    false-confirmation is the Phase-4/5 gate metric named in ROADMAP.md §5.
  - guard-refusal rate: scans every step_NN.json's "warnings" for the pre-fire TOCTOU
    guard's own signal (agent_loop_holo.py's GUARD_REFUSE_LIMIT machinery) -- not
    previously aggregated anywhere, only ever hand-counted in session docs.
  - per-step latency: actor (per_step_wall_time_s) and, where shadow verification ran,
    verify latency (verification.wall_time_s) as its OWN distribution plus the summed
    cost of a verified step -- holo3.1 serves with --parallel 1 (kvm_agent/llm/serving.py
    confirmed this 2026-07-23), so a verify call serializes behind the actor call rather
    than overlapping it.
  - honest-refusal vs budget-exhaustion: split by the recorder's own abort `note`
    (unambiguous: "max_steps reached" / "stuck limit hit" / "no progress: ..." /
    "target region unstable...") vs a `finished=True` answer whose text reads like a
    refusal (a small keyword heuristic -- reported AS a heuristic, not a certainty;
    ROADMAP.md §5 flags this pair as measurable-but-uncomputed).

NOT computed here (out of scope for this tool): grounding rate (ROADMAP.md's other
Phase-5 gate metric) -- that needs a grounding-specific oracle over the `element`
field, a different eval shape from a postcondition check.

KNOWN CAVEAT reading `grade` verbatim: `runs/battery_20260721_235153/results.json`'s
paint_line row is recorded `grade: "pass"` -- a protest pass forced by the pre-void-grade
p/f-only vocabulary (docs/SESSION_2026-07-22_first_complete_battery.md finding 1), whose
documented HONEST reading is void (excluded, not a pass). That results.json is immutable
evidence (AGENTS.md) and is deliberately not retro-edited, so this tool reads it exactly
as recorded and does NOT special-case it -- --all's completion rate over that battery
will read one row higher than PROJECT_STATE.md's "4/4 (1 void)" figure. Every battery run
since 2026-07-22 uses the current void-grade mechanism and has no such row.

Artifacts (AGENTS.md §1): runs/battery_metrics_<ts>/report.json
"""
import argparse
import glob
import os
import re
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # tools/, for verify_replay

from kvm_agent.config import CFG
from verify_replay import read_json, resolve_run_dir, task_run_dirs

BATTERY_DIR_RE = re.compile(r"^battery_(\d{8}_\d{6})$")

# note-text prefixes recorder.finish() uses on every ABORT path (agent_loop_holo.py) --
# unambiguous, no keyword guessing needed for this half of the refusal/exhaustion split.
ABORT_NOTE_PREFIXES = ("max_steps reached", "stuck limit hit", "no progress:",
                      "target region unstable")
# best-effort ONLY: a finished=True answer whose text reads like the model declining
# the task rather than completing it. Reported as a heuristic in the output, never as
# a certainty -- this is exactly the "not yet a computed rate" ROADMAP.md §5 flags.
REFUSAL_KEYWORDS = ("cannot", "unable", "not possible", "no such", "does not exist",
                   "doesn't exist", "isn't installed", "is not installed", "infeasible")


def find_battery_dirs(runs_dir, spec=None):
    """battery_<ts> (results-summary) dirs only -- deliberately distinct from the
    per-task battery_<task_id>_<ts> RunRecorder dirs, which don't match
    BATTERY_DIR_RE (task ids are alphabetic, not \\d{8}_\\d{6})."""
    names = [n for n in os.listdir(runs_dir) if BATTERY_DIR_RE.match(n)]
    if spec:
        names = [n for n in names if spec in n]
    return sorted(names, key=lambda n: os.path.getmtime(os.path.join(runs_dir, n)))


def _pct(n, d):
    return round(100.0 * n / d, 1) if d else None


def _stats(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return {"n": len(vals), "median": round(statistics.median(vals), 2),
            "min": round(min(vals), 2), "max": round(max(vals), 2)}


def _classify_ending(row, summary):
    """(bucket, detail) for the honest-refusal/budget-exhaustion split. Buckets:
    'answered' (a normal-looking finished=True answer), 'honest_refusal_heuristic'
    (finished=True but the text reads like a decline -- HEURISTIC), 'budget_exhaustion'
    (an abort note the recorder itself wrote, unambiguous), 'aborted_other' (finished
    False with no recognized note -- shouldn't normally happen, flagged if it does)."""
    if row.get("finished"):
        text = (row.get("answer_text") or "").lower()
        if any(k in text for k in REFUSAL_KEYWORDS):
            return "honest_refusal_heuristic", row.get("answer_text")
        return "answered", None
    note = (summary or {}).get("note") or ""
    if any(note.startswith(p) for p in ABORT_NOTE_PREFIXES):
        return "budget_exhaustion", note
    return "aborted_other", note


def analyze_battery(battery_dir, runs_dir):
    """One battery_<ts> dir -> {rows: [...], score, tasks_file}, or None if this dir has
    no results.json at all (pre-2026-07-21 layout wrote battery_summary.json instead --
    several such dirs exist in this repo's own runs/ archive). Returning None rather
    than an empty-but-"analyzed" result matters for --all: a silently-skipped dir would
    otherwise appear in the printed battery list as if it had actually contributed rows.

    Each row joins a results.json entry with its own RunRecorder summary + step
    records, resolved via verify_replay.py's own tag/timestamp matching (the same join
    D-a's offline eval uses, reused rather than re-implemented)."""
    results_path = os.path.join(battery_dir, "results.json")
    if not os.path.isfile(results_path):
        print(f"[metrics] SKIPPING {os.path.basename(battery_dir)}: no results.json "
             f"(pre-2026-07-21 battery_summary.json layout, or an incomplete run)")
        return None
    payload = read_json(results_path, {})
    battery_ts = BATTERY_DIR_RE.match(os.path.basename(battery_dir)).group(1)
    by_tag = task_run_dirs(runs_dir)
    claimed = set()
    rows = []
    for row in payload.get("results", []):
        run_name = resolve_run_dir(by_tag, claimed, row.get("run_tag"), battery_ts)
        summary, steps = {}, []
        if run_name:
            run_dir = os.path.join(runs_dir, run_name)
            summary = read_json(os.path.join(run_dir, "summary.json"), {}) or {}
            for f in sorted(glob.glob(os.path.join(run_dir, "step_*.json"))):
                steps.append(read_json(f, {}) or {})
        else:
            print(f"[metrics] WARNING: no run dir found for {row.get('run_tag')} "
                 f"in {battery_dir} -- that row's per-step data is unavailable")
        rows.append({"row": row, "run": run_name, "summary": summary, "steps": steps})
    return {"battery_dir": os.path.basename(battery_dir),
            "score": payload.get("score"), "tasks_file": payload.get("tasks_file"),
            "rows": rows}


def aggregate(analyses):
    """Every metric this tool reports, over the merged rows of one or more batteries."""
    rows = [r for a in analyses for r in a["rows"]]
    graded = [r for r in rows if r["row"].get("grade") in ("pass", "fail")]
    passes = sum(1 for r in graded if r["row"]["grade"] == "pass")
    voids = sum(1 for r in rows if r["row"].get("grade") == "void")

    steps_finished = [r["summary"].get("steps_taken") for r in rows if r["row"].get("finished")]
    steps_aborted = [r["summary"].get("steps_taken") for r in rows if not r["row"].get("finished")]

    false_finished = [r for r in graded if r["row"].get("finished") and r["row"]["grade"] == "fail"]
    false_finished_rate = _pct(len(false_finished),
                              sum(1 for r in graded if r["row"].get("finished")))

    # Verifier agreement -- only over rows that HAVE an auto_grade (shadow ran and the
    # task actually reached a finished claim) AND a human pass/fail (voids excluded,
    # same reasoning as grade_task's own denominator).
    verified = [r for r in graded if r["row"].get("auto_grade") in ("pass", "fail")]
    agreement = [r for r in verified if r["row"]["auto_grade"] == r["row"]["grade"]]
    false_refusals = [r for r in verified
                      if r["row"]["auto_grade"] == "fail" and r["row"]["grade"] == "pass"]
    false_confirmations = [r for r in verified
                           if r["row"]["auto_grade"] == "pass" and r["row"]["grade"] == "fail"]

    guard_refusal_steps = 0
    total_steps = 0
    for r in rows:
        for step in r["steps"]:
            total_steps += 1
            warnings = (step.get("action") or {}).get("warnings") or []
            if any(str(w).startswith("guard_refusal") for w in warnings):
                guard_refusal_steps += 1

    actor_wall = [t for r in rows for t in (r["summary"].get("per_step_wall_time_s") or [])]
    actor_prompt_tok = [t for r in rows
                        for t in (r["summary"].get("per_step_prompt_tokens") or []) if t]
    verify_wall, verify_combined = [], []
    for r in rows:
        for step in r["steps"]:
            v = step.get("verification")
            if v and v.get("wall_time_s") is not None:
                verify_wall.append(v["wall_time_s"])
                if step.get("wall_time_s") is not None:
                    verify_combined.append(step["wall_time_s"] + v["wall_time_s"])

    endings = {}
    for r in rows:
        bucket, detail = _classify_ending(r["row"], r["summary"])
        endings.setdefault(bucket, []).append(
            {"task_id": r["row"].get("task_id"), "detail": detail})

    return {
        "batteries": [a["battery_dir"] for a in analyses],
        "total_rows": len(rows),
        "completion_rate": {"passes": passes, "denominator": len(graded), "voids": voids,
                            "pct": _pct(passes, len(graded))},
        "steps_to_completion": {"finished": _stats(steps_finished),
                                "aborted": _stats(steps_aborted)},
        "false_finished": {
            "count": len(false_finished), "of_finished": sum(1 for r in graded if r["row"].get("finished")),
            "rate_pct": false_finished_rate,
            "tasks": [r["row"]["task_id"] for r in false_finished]},
        "verifier": (None if not verified else {
            "n_compared": len(verified),
            "agreement_pct": _pct(len(agreement), len(verified)),
            "false_refusals": {"count": len(false_refusals),
                               "rate_pct": _pct(len(false_refusals),
                                                sum(1 for r in verified if r["row"]["grade"] == "pass")),
                               "tasks": [r["row"]["task_id"] for r in false_refusals]},
            "false_confirmations": {"count": len(false_confirmations),
                                    "rate_pct": _pct(len(false_confirmations),
                                                    sum(1 for r in verified if r["row"]["grade"] == "fail")),
                                    "tasks": [r["row"]["task_id"] for r in false_confirmations]},
        }),
        "guard_refusal_rate": {"steps_with_refusal": guard_refusal_steps,
                               "total_steps": total_steps,
                               "pct": _pct(guard_refusal_steps, total_steps)},
        "latency_s": {"actor_per_step": _stats(actor_wall),
                     "verify_per_step": _stats(verify_wall),
                     "verified_step_combined": _stats(verify_combined)},
        "actor_prompt_tokens_per_step": _stats(actor_prompt_tok),
        "endings": {bucket: {"count": len(items), "tasks": items}
                   for bucket, items in endings.items()},
    }


def _print_report(report):
    print(f"[metrics] batteries: {', '.join(report['batteries'])}")
    c = report["completion_rate"]
    print(f"[metrics] completion: {c['passes']}/{c['denominator']} "
         f"({c['pct']}%)" + (f", {c['voids']} void" if c["voids"] else ""))
    s = report["steps_to_completion"]
    print(f"[metrics] steps-to-completion: finished {s['finished']}, aborted {s['aborted']}")
    ff = report["false_finished"]
    print(f"[metrics] false-\"finished\" rate: {ff['count']}/{ff['of_finished']} "
         f"({ff['rate_pct']}%){' -- ' + ', '.join(ff['tasks']) if ff['tasks'] else ''}")
    v = report["verifier"]
    if v is None:
        print("[metrics] verifier: no shadow-verification data in this battery "
             "(run tools/battery.py <tasks> shadow)")
    else:
        fr, fc = v["false_refusals"], v["false_confirmations"]
        print(f"[metrics] verifier agreement: {v['agreement_pct']}% over {v['n_compared']} "
             f"compared")
        print(f"[metrics]   false-refusal (gates slice D-c): {fr['count']} "
             f"({fr['rate_pct']}%){' -- ' + ', '.join(fr['tasks']) if fr['tasks'] else ''}")
        print(f"[metrics]   false-confirmation (Phase-4/5 gate): {fc['count']} "
             f"({fc['rate_pct']}%){' -- ' + ', '.join(fc['tasks']) if fc['tasks'] else ''}")
    g = report["guard_refusal_rate"]
    print(f"[metrics] guard-refusal rate: {g['steps_with_refusal']}/{g['total_steps']} "
         f"steps ({g['pct']}%)")
    lat = report["latency_s"]
    print(f"[metrics] actor latency/step: {lat['actor_per_step']}")
    if lat["verify_per_step"]:
        print(f"[metrics] verify latency/step: {lat['verify_per_step']}")
        print(f"[metrics] verified-step combined (serialized, --parallel 1): "
             f"{lat['verified_step_combined']}")
    print("[metrics] endings (honest-refusal heuristic is NOT authoritative):")
    for bucket, info in report["endings"].items():
        print(f"[metrics]   {bucket}: {info['count']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("battery", nargs="?", default=None,
                    help="a battery_<ts> dir path, or a prefix to match (default: latest)")
    ap.add_argument("--all", action="store_true",
                    help="merge every battery_<ts> ever run, not just one")
    args = ap.parse_args()

    runs_dir = CFG.runs_dir
    if args.all:
        names = find_battery_dirs(runs_dir)
        if not names:
            sys.exit(f"no battery_<ts> directories found under {runs_dir}")
    else:
        names = find_battery_dirs(runs_dir, spec=args.battery)
        if not names:
            sys.exit(f"no battery_<ts> directories found under {runs_dir}"
                     + (f" matching {args.battery!r}" if args.battery else ""))
        names = names[-1:]   # latest only, unless --all

    analyses = [analyze_battery(os.path.join(runs_dir, n), runs_dir) for n in names]
    analyses = [a for a in analyses if a is not None]
    if not analyses:
        sys.exit("no analyzable battery directories (all matched dirs pre-date "
                 "results.json -- see the SKIPPING warnings above)")
    report = aggregate(analyses)
    _print_report(report)

    out_dir = os.path.join(runs_dir, f"battery_metrics_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(out_dir, exist_ok=True)
    import json
    with open(os.path.join(out_dir, "report.json"), "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"[metrics] -> {out_dir}/report.json")


if __name__ == "__main__":
    main()
