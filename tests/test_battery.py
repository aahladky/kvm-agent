"""
test_battery.py — OFFLINE test for the battery runner's pure parts (task loading,
grading input, results writing). The interactive runner itself is live-verified.

    python tests/test_battery.py
"""
import sys, os, json, tempfile, builtins
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import battery


def test_load_tasks_and_write_results():
    with tempfile.TemporaryDirectory() as td:
        good = os.path.join(td, "tasks.json")
        with open(good, "w") as f:
            json.dump([{"id": "t1", "instruction": "do thing"}], f)
        tasks = battery.load_tasks(good)
        assert len(tasks) == 1 and tasks[0]["id"] == "t1", "load_tasks returns tasks"
        assert tasks[0]["max_steps"] == 15, "max_steps defaults to 15"
        assert tasks[0]["reset"] == {
            "cleanup_files": [], "setting_resets": [],
            "application_reset": "battery-apps"}

        bad = os.path.join(td, "bad.json")
        with open(bad, "w") as f:
            json.dump([{"instruction": "no id"}], f)
        rejected = False
        try:
            battery.load_tasks(bad)
        except AssertionError:
            rejected = True
        assert rejected, "task without id rejected"

        out = os.path.join(td, "results.json")
        battery.write_results(out, {"results": [], "score": "0/0"})
        with open(out) as f:
            assert json.load(f)["score"] == "0/0", "write_results round-trips"


# grading: empty input re-asks (a grade can never be silently recorded, finding #8);
# 'p note' -> pass with note; 'f' -> fail with empty note
def test_grade_task_input_handling():
    real_input = builtins.input
    answers = iter(["", "p looks good"])
    builtins.input = lambda prompt="": next(answers)
    try:
        v = battery.grade_task({"id": "t1"}, {"finished": True, "answer_text": ""})
    finally:
        builtins.input = real_input
    assert v == {"grade": "pass", "note": "looks good"}, "grade_task re-asks on empty, then passes"

    answers = iter(["f fell over"])
    builtins.input = lambda prompt="": next(answers)
    try:
        v = battery.grade_task({"id": "t2"}, {"finished": False, "answer_text": ""})
    finally:
        builtins.input = real_input
    assert v == {"grade": "fail", "note": "fell over"}, "grade_task fail with note"


# void grade (2026-07-22, first-complete-battery review): 'v' excludes an infeasible
# task from the denominator, but ONLY with a note -- a bare 'v' re-asks. Before this,
# p/f-only forced the operator to record a protest "pass" on paint_line (no paint app
# on the GNOME target), which inflated the score to 5/5.
def test_grade_task_void_requires_note():
    real_input = builtins.input
    answers = iter(["v", "v no paint app installed"])
    builtins.input = lambda prompt="": next(answers)
    try:
        v = battery.grade_task({"id": "t3"}, {"finished": False, "answer_text": ""})
    finally:
        builtins.input = real_input
    assert v == {"grade": "void", "note": "no paint app installed"}, \
        "bare 'v' re-asks; 'v <note>' records a void"


def test_payload_void_excluded_from_denominator():
    tasks = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    results = [{"task_id": "a", "grade": "pass", "note": ""},
               {"task_id": "b", "grade": "pass", "note": ""},
               {"task_id": "c", "grade": "void", "note": "infeasible"}]
    p = battery.make_payload("20260722_000000", "tasks.json", False, tasks, results)
    assert p["score"] == "2/2 (1 void)", f"void leaves the denominator, stays visible: {p['score']}"
    assert p["complete"] is True, "voids still count as graded for completeness"
    # a void can never masquerade as a pass in the numerator
    only_void = [{"task_id": "a", "grade": "void", "note": "n/a"}]
    p2 = battery.make_payload("20260722_000000", "tasks.json", False, tasks[:1], only_void)
    assert p2["score"] == "0/0 (1 void)", f"all-void battery scores 0/0, got {p2['score']}"


# --- roadmap Phase 2 slice D-b: the oracle's verdict travels alongside the human
# grade, never replacing it. auto_grade_from_verdict is the pure mapping function. ---
def test_auto_grade_maps_satisfied_to_pass_fail():
    grade, evidence = battery.auto_grade_from_verdict(
        {"satisfied": True, "evidence": "calculator shows 56", "wall_time_s": 0.1,
         "usage": {}})
    assert (grade, evidence) == ("pass", "calculator shows 56")

    grade, evidence = battery.auto_grade_from_verdict(
        {"satisfied": False, "evidence": "still shows 0", "wall_time_s": 0.1,
         "usage": {}})
    assert (grade, evidence) == ("fail", "still shows 0")


def test_auto_grade_none_is_not_a_silent_pass():
    """Fail-closed, matching grade_task's own p/f/v discipline: an oracle that didn't
    answer must never read as a pass -- it must be indistinguishable from 'no verdict
    at all' in the numerator, only distinguishable in the evidence text."""
    grade, evidence = battery.auto_grade_from_verdict(
        {"satisfied": None, "evidence": "verifier call raised: timeout",
         "wall_time_s": 0.0, "usage": {}})
    assert grade is None
    assert "timeout" in evidence


def test_main_rejects_unknown_verify_mode_before_touching_anything():
    """Validated before load_tasks() is even reached -- a real task file need not
    exist on disk to discover a typo'd verify_mode (fail fast on bad args, the same
    discipline as run()'s eager MAX_HISTORY_IMAGES/verify_mode checks)."""
    real_argv = sys.argv
    sys.argv = ["battery.py", "/nonexistent/tasks.json", "bogus_mode"]
    try:
        try:
            battery.main()
        except SystemExit as e:
            assert e.code == 2, "argparse rejects the invalid mode before loading tasks"
        else:
            raise AssertionError("main() must exit on an unknown verify_mode")
    finally:
        sys.argv = real_argv


def test_auto_grade_no_verdict_at_all():
    """verify_mode='off' (no verifier ever ran) and a task that never reached a
    finished claim both look identical here: (None, None)."""
    assert battery.auto_grade_from_verdict(None) == (None, None)


def test_d_c_verifier_grade_fails_closed():
    assert battery.verifier_grade(
        {"satisfied": True, "evidence": "visible"}) == {
            "grade": "pass", "note": "visible"}
    assert battery.verifier_grade(
        {"satisfied": False, "evidence": "missing"}) == {
            "grade": "fail", "note": "missing"}
    assert battery.verifier_grade(
        {"satisfied": None, "evidence": "timeout"}) == {
            "grade": "fail", "note": "timeout"}
    missing = battery.verifier_grade(None)
    assert missing["grade"] == "fail" and "no verifier verdict" in missing["note"]


def test_d_c_cli_defaults_to_gate_and_validates_unattended_contract():
    args = battery.parse_args(["tasks.json"])
    assert args.verify_mode == "gate" and args.human is False
    assert args.reset_strategy == "manual-power-cycle"
    args = battery.parse_args(["tasks.json", "--no-reboot"])
    assert args.verify_mode == "gate" and args.reset_strategy == "none"
    assert args.spot_check_pct == 0
    args = battery.parse_args(["tasks.json", "--reset-strategy", "cleanup"])
    assert args.reset_strategy == "cleanup" and args.spot_check_pct == 10
    try:
        battery.parse_args(["tasks.json", "off"])
    except SystemExit:
        pass
    else:
        raise AssertionError("off without --human has no primary grader and must fail")


def test_payload_score_is_fail_closed():
    """Second review #8 (2026-07-21): an abandoned battery must not read as complete --
    the score denominator is ALL tasks, not just the graded ones (a real Ctrl-C
    battery recorded '1/1' before this)."""
    tasks = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    results = [{"task_id": "a", "grade": "pass", "note": ""}]
    p = battery.make_payload("20260721_000000", "tasks.json", False, tasks, results)
    assert p["score"] == "1/3", f"score over total tasks, got {p['score']}"
    assert p["total_tasks"] == 3 and p["graded"] == 1, "counts exposed"
    assert p["complete"] is False, "partial battery is marked incomplete"
    p2 = battery.make_payload("20260721_000000", "tasks.json", False, tasks[:1], results)
    assert p2["score"] == "1/1" and p2["complete"] is True, \
        "a fully-graded battery reads complete"


def test_payload_records_experiment_configuration():
    cfg = {"verify_mode": "gate", "grader": "verifier",
           "spot_check_pct": 10.0, "reset_strategy": "cleanup"}
    payload = battery.make_payload("20260723_000000", "tasks.json", False,
                                   [{"id": "a"}], [], cfg,
                                   [{"task_id": "a", "satisfied": True}])
    assert payload["run_config"] == cfg
    assert payload["reset_events"][0]["satisfied"] is True


if __name__ == "__main__":
    import sys, traceback
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
