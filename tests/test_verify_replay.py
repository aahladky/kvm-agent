"""
test_verify_replay.py — OFFLINE tests for the replay eval's case builder
(tools/verify_replay.py, roadmap Phase 2 slice D-a).

The model is never called here: build_cases() is pure filesystem logic over a synthetic
runs/ tree. What's under test is the LABELLING, because a mislabelled eval set produces
confident nonsense — the first live replay run scored three "misses" that were purely
label error (runs/verify_replay_20260722_235815), which is what OBSERVATION_TASK_RE now
encodes.

    python -m pytest tests/test_verify_replay.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.verify_replay import (
    GENERIC_CLAIM, adversarial_variants, build_cases, resolve_run_dir, score,
    task_run_dirs,
)


def _run_dir(root, name, goal, steps=2, success=True):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    for i in range(steps):
        open(os.path.join(d, f"step_{i:02d}.png"), "wb").close()
    with open(os.path.join(d, "meta.json"), "w") as f:
        json.dump({"goal": goal, "screen_size": [1280, 720]}, f)
    with open(os.path.join(d, "summary.json"), "w") as f:
        json.dump({"success": success, "steps_taken": steps}, f)
    return d


def _battery(root, ts, rows):
    d = os.path.join(root, f"battery_{ts}")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "results.json"), "w") as f:
        json.dump({"results": rows}, f)


def _archive():
    """A synthetic runs/ tree covering every case class the builder must handle."""
    root = tempfile.mkdtemp(prefix="verify_replay_test_")
    _battery(root, "20260101_000000", [
        {"task_id": "editor", "run_tag": "battery_editor", "grade": "pass",
         "finished": True, "answer_text": "saved it",
         "instruction": "Open a text editor and save the file as hello.txt"},
        {"task_id": "painter", "run_tag": "battery_painter", "grade": "void",
         "finished": False, "answer_text": "",
         "instruction": "Draw a line in a paint app"},
        {"task_id": "clock", "run_tag": "battery_clock", "grade": "pass",
         "finished": True, "answer_text": "It says 09:41",
         "instruction": "Read the clock at the top of the screen and tell me what it says"},
    ])
    _run_dir(root, "battery_editor_20260101_000100", "editor goal", steps=3, success=True)
    _run_dir(root, "battery_painter_20260101_000200", "painter goal", steps=4, success=False)
    _run_dir(root, "battery_clock_20260101_000300", "clock goal", steps=1, success=True)
    return root


def test_positives_are_graded_pass_and_finished_with_the_claim_attached():
    root = _archive()
    try:
        cases, _ = build_cases(root)
        pos = [c for c in cases if c["kind"] == "positive"]
        assert {c["task_id"] for c in pos} == {"editor", "clock"}
        editor = next(c for c in pos if c["task_id"] == "editor")
        assert editor["expected"] is True
        assert editor["claim"] == "saved it", \
            "the model's own answer must reach the oracle -- some postconditions are only " \
            "checkable against the claim"
        assert editor["frame"].endswith("step_02.png"), "positives use the LAST frame"
    finally:
        shutil.rmtree(root)


def test_a_run_that_never_claimed_done_is_a_negative_not_a_positive():
    """The 2026-07-21 paint_line row is graded `pass` (a protest grade, later understood
    as void) while finished=False. Grade alone must not promote it to a positive."""
    root = _archive()
    try:
        cases, _ = build_cases(root)
        painter = [c for c in cases if c["task_id"] == "painter"]
        assert all(c["kind"] == "negative" for c in painter)
        finals = [c for c in painter if c["source"] == "final_frame"]
        assert len(finals) == 1 and finals[0]["expected"] is False
        assert finals[0]["claim"] == ""
    finally:
        shutil.rmtree(root)


def test_step_00_is_a_negative_for_ordinary_tasks():
    root = _archive()
    try:
        cases, _ = build_cases(root)
        step0 = [c for c in cases if c["source"] == "step_00"]
        assert {c["task_id"] for c in step0} == {"editor", "painter"}
        assert all(c["expected"] is False and c["claim"] == "" for c in step0)
        assert all(c["frame"].endswith("step_00.png") for c in step0)
    finally:
        shutil.rmtree(root)


def test_observation_tasks_are_skipped_at_step_00_with_a_reason():
    """An observation task's postcondition ("the clock is readable") already holds on the
    pre-task desktop, so step_00 is NOT evidence of an unfinished screen. Scoring it as a
    negative punishes the oracle for being right."""
    root = _archive()
    try:
        cases, skipped = build_cases(root)
        assert not any(c["task_id"] == "clock" and c["source"] == "step_00" for c in cases)
        note = next(s for s in skipped if s["task_id"] == "clock")
        assert "observation task" in note["reason"]
    finally:
        shutil.rmtree(root)


def test_ungraded_failed_runs_are_inferred_negatives_and_successes_are_ignored():
    """A run belonging to no graded battery is scored ONLY if it failed -- an ungraded
    success has no human grade to trust, so it is not evidence in either direction."""
    root = _archive()
    try:
        _run_dir(root, "battery_orphan_fail_20260102_000000", "an unfinished goal",
                 steps=2, success=False)
        _run_dir(root, "battery_orphan_ok_20260102_000100", "an ungraded success",
                 steps=2, success=True)
        cases, _ = build_cases(root)
        kinds = {c["task_id"]: c["kind"] for c in cases}
        assert kinds.get("battery_orphan_fail") == "negative_inferred"
        assert "battery_orphan_ok" not in kinds, \
            "an ungraded success is not evidence -- it must not be scored"
        inferred = next(c for c in cases if c["kind"] == "negative_inferred")
        assert inferred["instruction"] == "an unfinished goal", "instruction from meta.json"
        assert inferred["expected"] is False
    finally:
        shutil.rmtree(root)


def test_each_run_dir_is_claimed_by_exactly_one_battery_row():
    """The same task tag repeats across batteries; a naive 'latest dir' would score one
    run three times and never look at the others."""
    root = tempfile.mkdtemp(prefix="verify_replay_test_")
    try:
        row = {"task_id": "calc", "run_tag": "battery_calc", "grade": "pass",
               "finished": True, "answer_text": "56", "instruction": "compute 7 times 8"}
        _battery(root, "20260101_000000", [row])
        _battery(root, "20260102_000000", [row])
        _run_dir(root, "battery_calc_20260101_000100", "g")
        _run_dir(root, "battery_calc_20260102_000100", "g")
        cases, _ = build_cases(root)
        runs = [c["run"] for c in cases if c["kind"] == "positive"]
        assert sorted(runs) == ["battery_calc_20260101_000100",
                                "battery_calc_20260102_000100"], \
            "each battery row must resolve to its OWN run dir"
    finally:
        shutil.rmtree(root)


def test_resolve_run_dir_never_reaches_back_before_its_battery():
    by_tag = task_run_dirs_stub = {"battery_x": [("20260101_000000", "battery_x_20260101_000000")]}
    assert resolve_run_dir(by_tag, set(), "battery_x", "20260102_000000") is None, \
        "a dir predating the battery cannot belong to it"
    assert resolve_run_dir(by_tag, set(), "battery_x", "20260101_000000") == \
        "battery_x_20260101_000000"


def test_adversarial_variants_pair_unfinished_screens_with_a_confident_false_claim():
    """Without these the eval is confounded: every positive carries a claim and every
    negative carries none, so the separation could be reading claim-presence rather than
    pixels — and the case that gates D-c (unfinished screen + confident "I'm done") would
    go untested."""
    root = _archive()
    try:
        cases, _ = build_cases(root, adversarial=True)
        adv = [c for c in cases if c["kind"] == "negative_adversarial"]
        base = [c for c in cases if c["kind"] in ("negative", "negative_inferred")]
        assert len(adv) == len(base), "every unfinished-screen frame gets a claimed twin"
        assert all(c["expected"] is False for c in adv), \
            "a claim does not change the truth: the pixels still don't show it done"
        assert all(c["claim"] for c in adv)
        assert all(c["source"].endswith("+claim") for c in adv)
    finally:
        shutil.rmtree(root)


def test_adversarial_claim_is_borrowed_from_the_same_task_when_one_exists():
    """The most plausible lie available is the real thing the model said when it genuinely
    did finish that same task."""
    root = _archive()
    try:
        # editor has a graded-pass run (claim "saved it"); painter has none.
        cases, _ = build_cases(root, adversarial=True)
        adv = {c["task_id"]: c for c in cases if c["kind"] == "negative_adversarial"}
        assert adv["editor"]["claim"] == "saved it"
        assert adv["editor"]["claim_borrowed_from_same_task"] is True
        assert adv["painter"]["claim"] == GENERIC_CLAIM
        assert adv["painter"]["claim_borrowed_from_same_task"] is False
    finally:
        shutil.rmtree(root)


def test_adversarial_cases_are_off_by_default():
    root = _archive()
    try:
        cases, _ = build_cases(root)
        assert not any(c["kind"] == "negative_adversarial" for c in cases)
    finally:
        shutil.rmtree(root)


def test_adversarial_variants_never_derive_from_a_positive():
    """A positive's frame genuinely shows completion; pairing it with a claim and
    expecting False would be a fabricated failure."""
    adv = adversarial_variants([
        {"kind": "positive", "task_id": "t", "source": "final_frame", "claim": "done"},
        {"kind": "negative", "task_id": "t", "source": "step_00", "claim": ""},
    ])
    assert len(adv) == 1 and adv[0]["source"] == "step_00+claim"


def test_score_keeps_inferred_negatives_out_of_the_headline_numbers():
    rows = [
        {"kind": "positive", "source": "final_frame", "satisfied": True, "expected": True},
        {"kind": "positive", "source": "final_frame", "satisfied": False, "expected": True},
        {"kind": "negative", "source": "step_00", "satisfied": False, "expected": False},
        {"kind": "negative_inferred", "source": "final_frame_ungraded",
         "satisfied": True, "expected": False},
        {"kind": "negative", "source": "step_00", "satisfied": None, "expected": False},
    ]
    s = score(rows)
    assert s["positives"] == 2 and s["false_refusals"] == 1
    assert s["false_refusal_rate"] == 0.5
    assert s["negatives"] == 2 and s["negatives_correct"] == 1
    assert s["inferred_negatives"] == 1 and s["inferred_negatives_correct"] == 0, \
        "the inferred bucket is reported, never folded into the graded numbers"
    assert s["unanswered"] == 1, "satisfied=None is counted, not silently scored as wrong"
    assert s["by_source"]["step_00"] == {"n": 2, "correct": 1, "unanswered": 1}


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except Exception:
            fails += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print("\n" + ("ALL PASS" if not fails else f"{fails} FAILED"))
    sys.exit(1 if fails else 0)
