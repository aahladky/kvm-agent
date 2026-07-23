#!/usr/bin/env python3
"""
verify_replay.py — OFFLINE eval of the postcondition oracle against the graded run
archive (roadmap Phase 2 slice D-a,
docs/PLAN_2026-07-22_phase2_subgoal_verification.md).

This is the go/no-go on whether a Holo-backed oracle works AT ALL, and it costs no rig
time: every frame it judges is already on disk with a human grade attached. It is
AGENTS.md §2.4's offline-replay mechanism (saved frame -> model, no pipeline) pointed at
the verifier instead of the actor.

    python tools/verify_replay.py                      # the whole archive
    python tools/verify_replay.py --limit 6            # smoke run
    python tools/verify_replay.py --cases positives    # positives | negatives | all

THE EVAL SET, entirely from runs/ (join: runs/battery_<ts>/results.json carries run_tag +
human grade + answer_text + instruction; the per-task runs/<run_tag>_<ts>/ carries the
frames):

  POSITIVES  (expected satisfied=True)  the LAST step_NN.png of every human-graded `pass`
             run that ended finished=True. Measures FALSE-REFUSAL — the number that gates
             slice D-c, because a false-refusing oracle turns a true pass into a fail.

  NEGATIVES  (expected satisfied=False) two sources:
             (a) the last step_NN.png of every run that ended finished=False — the model
                 never claimed done, and the screen agrees;
             (b) step_00.png of EVERY run — the pre-task desktop. A free, correctly
                 labelled "not done yet" frame for every task in the archive, and what
                 makes the negative set big enough to mean anything.

WHY THE LAST step_NN.png IS THE RIGHT FRAME: step_NN.png is the frame the model saw
BEFORE deciding step NN (RunRecorder's contract), so the last one is what it was looking
at when it decided to answer `finished`. The live D-b wiring will instead verify the
`after` frame of the finished action — the same screen, since `finished` changes nothing.

HONEST LIMIT, stated in the output too: no archived run contains a FALSE `finished`
claim (the model has never claimed done and been wrong on a graded task). So the
negatives measure "does the oracle recognise an unfinished screen", NOT a true
false-confirmation rate. That number needs D-b's harder tasks.

Artifacts (AGENTS.md §1): runs/verify_replay_<ts>/results.json
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2

from kvm_agent.config import CFG
from kvm_agent.hardware.env import model_input_jpeg
from kvm_agent.models.holo import HoloVerifier, jpeg_bytes_to_data_url

TS_RE = re.compile(r"_(\d{8}_\d{6})$")

# OBSERVATION TASKS: ones whose postcondition is "the information is on screen and
# reported", not "the screen was changed". For these, step_00 is NOT a negative -- the
# clock is already readable on the pre-task desktop, so the oracle answering satisfied=True
# there is CORRECT and the "nothing has been done yet" label is what's wrong. Found the
# hard way in the first replay run (runs/verify_replay_20260722_235815): all three
# step_00 misses were the two clock tasks, each with impeccable evidence ("The top bar of
# the screen displays the date and time as 'Jul 22 17:56'"). Excluded with the reason
# recorded rather than silently dropped or scored as oracle error.
OBSERVATION_TASK_RE = re.compile(
    r"\b(read|tell me what it says|what does .* say|report the)\b", re.I)


def _ts(name):
    m = TS_RE.search(name)
    return m.group(1) if m else ""


def battery_results(runs_dir):
    """Every runs/battery_<ts>/results.json, oldest first."""
    out = []
    for name in sorted(os.listdir(runs_dir)):
        path = os.path.join(runs_dir, name, "results.json")
        if re.fullmatch(r"battery_\d{8}_\d{6}", name) and os.path.isfile(path):
            with open(path) as f:
                out.append((name, _ts(name), json.load(f)))
    return out


def task_run_dirs(runs_dir):
    """Per-task RunRecorder dirs grouped by tag: {tag: [(ts, dirname), ...]} sorted."""
    by_tag = {}
    for name in sorted(os.listdir(runs_dir)):
        if not os.path.isdir(os.path.join(runs_dir, name)):
            continue
        ts = _ts(name)
        if not ts or re.fullmatch(r"battery_\d{8}_\d{6}", name):
            continue
        by_tag.setdefault(name[: -(len(ts) + 1)], []).append((ts, name))
    for v in by_tag.values():
        v.sort()
    return by_tag


def resolve_run_dir(by_tag, claimed, tag, battery_ts):
    """The per-task dir belonging to this battery row: the earliest unclaimed dir for the
    tag whose timestamp is at or after the battery's own start. Claiming matters — the
    same tag repeats across batteries, and a naive 'latest' would score one run three
    times and ignore the others."""
    for ts, name in by_tag.get(tag, []):
        if ts >= battery_ts and name not in claimed:
            claimed.add(name)
            return name
    return None


def step_pngs(run_dir):
    return sorted(n for n in os.listdir(run_dir) if re.fullmatch(r"step_\d+\.png", n))


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def frame_data_url(png_path):
    """PNG on disk -> the JPEG data URL the live path would have sent: same encoder, same
    CFG.holo_model_input_res, so the oracle sees what it would see in production."""
    frame = cv2.imread(png_path, cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError(f"could not decode {png_path}")
    return jpeg_bytes_to_data_url(model_input_jpeg(frame, CFG.holo_model_input_res))


GENERIC_CLAIM = ("# Task Completed Successfully\n\nAll requirements have been fulfilled. "
                 "The task is complete and verified on screen.")


def adversarial_variants(cases):
    """Pair every unfinished-screen frame with a CONFIDENT FALSE claim.

    Why this exists: without it the eval is confounded. Every positive carries the real
    answer_text ("# Task Completed Successfully...") and every negative carries claim="",
    so a clean positive/negative separation could be partly "confident claim present vs
    absent" rather than "screen shows it vs doesn't". Worse, the confound hides the case
    that actually matters at the D-c gate — an unfinished screen PLUS a confident "I'm
    done" claim is exactly what a false confirmation IS.

    The claim is borrowed from a successful run of the SAME task where one exists (the
    most plausible lie available: it is the real thing the model said when it genuinely
    did finish), else a generic completion boilerplate. Expected answer is unchanged:
    False. The pixels don't confirm it, so the oracle must not either.
    """
    success_claim = {}
    for c in cases:
        if c["kind"] == "positive" and c["claim"] and c["task_id"] not in success_claim:
            success_claim[c["task_id"]] = c["claim"]
    out = []
    for c in cases:
        if c["kind"] not in ("negative", "negative_inferred"):
            continue
        claim = success_claim.get(c["task_id"], GENERIC_CLAIM)
        out.append({**c, "kind": "negative_adversarial",
                    "source": c["source"] + "+claim",
                    "claim": claim,
                    "claim_borrowed_from_same_task": c["task_id"] in success_claim,
                    "expected": False})
    return out


def build_cases(runs_dir, adversarial=False):
    """Every (frame, question, claim, expected) the archive supports."""
    by_tag = task_run_dirs(runs_dir)
    claimed = set()
    cases, skipped = [], []
    for battery, battery_ts, payload in battery_results(runs_dir):
        for row in payload.get("results", []):
            tag = row.get("run_tag")
            run = resolve_run_dir(by_tag, claimed, tag, battery_ts)
            if not run:
                print(f"[replay] WARNING: no run dir for {battery}/{tag} -- skipped")
                continue
            run_dir = os.path.join(runs_dir, run)
            steps = step_pngs(run_dir)
            if not steps:
                print(f"[replay] WARNING: {run} has no step frames -- skipped")
                continue
            summary = read_json(os.path.join(run_dir, "summary.json"), {}) or {}
            meta = read_json(os.path.join(run_dir, "meta.json"), {}) or {}
            w, h = (meta.get("screen_size") or CFG.screen_size)[:2]
            instruction = row.get("instruction") or meta.get("goal") or ""
            finished = bool(row.get("finished"))
            grade = row.get("grade")
            common = {"battery": battery, "run": run, "task_id": row.get("task_id"),
                      "instruction": instruction, "grade": grade, "finished": finished,
                      "w": w, "h": h}

            # (b) pre-task desktop: nothing has been done yet -- for every run EXCEPT
            # observation tasks, whose postcondition already holds there (see
            # OBSERVATION_TASK_RE).
            if OBSERVATION_TASK_RE.search(instruction):
                skipped.append({**common, "source": "step_00",
                                "reason": "observation task: its postcondition is satisfied "
                                          "by the initial screen, so step_00 is not a negative"})
            else:
                cases.append({**common, "kind": "negative", "source": "step_00",
                              "frame": os.path.join(run_dir, steps[0]),
                              "claim": "", "expected": False})

            final = os.path.join(run_dir, steps[-1])
            if finished and grade == "pass":
                cases.append({**common, "kind": "positive", "source": "final_frame",
                              "frame": final, "claim": row.get("answer_text") or "",
                              "expected": True})
            elif not finished:
                # (a) the run ended without the model ever claiming done.
                cases.append({**common, "kind": "negative", "source": "final_frame",
                              "frame": final, "claim": "", "expected": False})
            # finished=True but graded fail/void: the archive has none today. Deliberately
            # NOT scored either way -- that is the false-confirmation case, and inventing
            # an expectation for it here would fabricate the very number this eval cannot
            # measure (see the module docstring).

    # (c) Runs that ended finished=False but belong to no graded battery (abandoned
    # batteries, and the whole 2026-07-18 Windows-era set). The model never claimed done,
    # so "incomplete" is a defensible label -- but it is INFERRED, not human-confirmed: a
    # run can hit max_steps having actually completed the task and merely failed to call
    # `answer`. Scored in their own bucket for exactly that reason; the graded cases above
    # are the load-bearing evidence.
    for tag, items in sorted(by_tag.items()):
        for ts, name in items:
            if name in claimed:
                continue
            run_dir = os.path.join(runs_dir, name)
            summary = read_json(os.path.join(run_dir, "summary.json"))
            if not summary or summary.get("success") is not False:
                continue    # never scored: no human grade AND no failure signal
            meta = read_json(os.path.join(run_dir, "meta.json"), {}) or {}
            instruction = meta.get("goal") or ""
            steps = step_pngs(run_dir)
            if not instruction or not steps:
                continue
            w, h = (meta.get("screen_size") or CFG.screen_size)[:2]
            cases.append({"battery": None, "run": name, "task_id": tag,
                          "instruction": instruction, "grade": None, "finished": False,
                          "w": w, "h": h, "kind": "negative_inferred",
                          "source": "final_frame_ungraded",
                          "frame": os.path.join(run_dir, steps[-1]),
                          "claim": "", "expected": False})
    if adversarial:
        cases.extend(adversarial_variants(cases))
    return cases, skipped


def score(rows):
    """Headline numbers over the HUMAN-GRADED cases, plus per-bucket and per-source
    breakdowns. The inferred-negative bucket is reported separately and never folded into
    the headline: its label comes from 'the model never claimed done', not from a human."""
    def hits(rs):
        return sum(1 for r in rs if r["satisfied"] is r["expected"])
    def bucket(kind):
        return [r for r in rows if r["kind"] == kind]

    pos, neg, inferred = bucket("positive"), bucket("negative"), bucket("negative_inferred")
    adv = bucket("negative_adversarial")
    by_source = {}
    for r in rows:
        b = by_source.setdefault(r["source"], {"n": 0, "correct": 0, "unanswered": 0})
        b["n"] += 1
        b["correct"] += int(r["satisfied"] is r["expected"])
        b["unanswered"] += int(r["satisfied"] is None)
    return {
        "positives": len(pos),
        "positives_correct": hits(pos),
        "false_refusals": sum(1 for r in pos if r["satisfied"] is not True),
        "false_refusal_rate": (round(1 - hits(pos) / len(pos), 3) if pos else None),
        "negatives": len(neg),
        "negatives_correct": hits(neg),
        "false_confirmations_on_negatives": sum(1 for r in neg if r["satisfied"] is True),
        "negative_detection_rate": (round(hits(neg) / len(neg), 3) if neg else None),
        "inferred_negatives": len(inferred),
        "inferred_negatives_correct": hits(inferred),
        # Claim-resistance: unfinished screen + a confident false "I'm done" claim. This
        # is what a false confirmation IS at the D-c gate, so it is scored on its own.
        "adversarial": len(adv),
        "adversarial_correct": hits(adv),
        "adversarial_false_confirmations": sum(1 for r in adv if r["satisfied"] is True),
        "claim_resistance_rate": (round(hits(adv) / len(adv), 3) if adv else None),
        "unanswered": sum(1 for r in rows if r["satisfied"] is None),
        "by_source": by_source,
        "total": len(rows),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--cases", choices=("all", "positives", "negatives", "adversarial"),
                    default="all")
    ap.add_argument("--adversarial", action="store_true",
                    help="also pair every unfinished-screen frame with a confident FALSE "
                         "claim (claim-resistance; implied by --cases adversarial)")
    ap.add_argument("--limit", type=int, default=0, help="score at most N cases (smoke run)")
    ap.add_argument("--target", default="local", help="model target (local|hosted)")
    args = ap.parse_args()

    runs_dir = CFG.runs_dir
    cases, skipped = build_cases(runs_dir,
                                 adversarial=args.adversarial or args.cases == "adversarial")
    for s in skipped:
        print(f"[replay] skipped {s['task_id']}/{s['source']}: {s['reason']}")
    if args.cases != "all":
        keep = {"positives": ("positive",),
                "negatives": ("negative", "negative_inferred"),
                "adversarial": ("negative_adversarial",)}[args.cases]
        cases = [c for c in cases if c["kind"] in keep]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        sys.exit("[replay] no cases built -- is runs/ populated with graded batteries?")

    out_dir = os.path.join(runs_dir, f"verify_replay_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(out_dir, exist_ok=True)
    print(f"[replay] {len(cases)} cases -> {out_dir}")

    verifier = HoloVerifier(target=args.target)
    rows = []
    for i, case in enumerate(cases):
        verdict = verifier.check(frame_data_url(case["frame"]), case["w"], case["h"],
                                 case["instruction"], claim=case["claim"])
        row = {**case, "satisfied": verdict.satisfied, "evidence": verdict.evidence,
               "correct": verdict.satisfied is case["expected"],
               "wall_time_s": round(verdict.wall_time_s, 2),
               "usage": verdict.usage}
        row["frame"] = os.path.relpath(case["frame"], runs_dir)
        rows.append(row)
        mark = "ok " if row["correct"] else ("?? " if verdict.satisfied is None else "MISS")
        print(f"[replay] {i + 1}/{len(cases)} {mark} {case['kind'][:3]}/{case['source']:<11} "
              f"{case['task_id']:<18} expected={case['expected']} "
              f"got={verdict.satisfied} ({row['wall_time_s']}s) :: {verdict.evidence[:90]}")
        # Incremental write: a crash or an interrupt must not lose the calls already paid for.
        with open(os.path.join(out_dir, "results.json"), "w") as f:
            json.dump({"started": os.path.basename(out_dir), "target": args.target,
                       "model": CFG.holo_model, "model_input_res": CFG.holo_model_input_res,
                       "cases_requested": args.cases, "score": score(rows),
                       "skipped": skipped,
                       "limitation": "no archived run has a FALSE finished claim, so the "
                                     "negatives measure unfinished-screen recognition, not "
                                     "a true false-confirmation rate (needs slice D-b)",
                       "results": rows}, f, indent=2, default=str)

    s = score(rows)
    print(f"\n[replay] positives {s['positives_correct']}/{s['positives']} correct "
          f"(false refusals: {s['false_refusals']}, rate {s['false_refusal_rate']})")
    print(f"[replay] negatives {s['negatives_correct']}/{s['negatives']} correct "
          f"(oracle said done on {s['false_confirmations_on_negatives']})")
    if s["adversarial"]:
        print(f"[replay] claim-resistance {s['adversarial_correct']}/{s['adversarial']} correct "
              f"(FALSE CONFIRMATIONS under a confident false claim: "
              f"{s['adversarial_false_confirmations']})")
    print(f"[replay] inferred negatives (ungraded, label inferred from never-claimed-done) "
          f"{s['inferred_negatives_correct']}/{s['inferred_negatives']} correct")
    for src, b in sorted(s["by_source"].items()):
        print(f"[replay]   by source: {src:<22} {b['correct']}/{b['n']} correct"
              + (f", {b['unanswered']} unanswered" if b["unanswered"] else ""))
    print(f"[replay] unanswered (satisfied=None): {s['unanswered']}")
    print(f"[replay] results -> {out_dir}/results.json")
    print("[replay] NOTE: negatives measure unfinished-screen recognition, not a true "
          "false-confirmation rate -- no archived run has a false `finished` claim.")


if __name__ == "__main__":
    main()
