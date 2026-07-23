"""
test_battery_metrics.py — OFFLINE tests for tools/battery_metrics.py's aggregation
logic (roadmap Phase 2 slice D-b). A synthetic runs/ tree, no real battery needed.

Numbers here are cross-checked in docs/SESSION_2026-07-23_phase2_slice_d_b_*.md
against the ACTUAL archive (runs/battery_20260722_222137/ etc.) -- this file only
protects the pure aggregation math against regression.

    python -m pytest tests/test_battery_metrics.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import battery_metrics as bm


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f)


def _run_dir(root, name, steps, summary_extra=None):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    for i, step in enumerate(steps):
        _write(os.path.join(d, f"step_{i:02d}.json"), step)
    summary = {"steps_taken": len(steps),
              "per_step_wall_time_s": [s.get("wall_time_s", 5.0) for s in steps]}
    if summary_extra:
        summary.update(summary_extra)
    _write(os.path.join(d, "summary.json"), summary)
    return d


def _battery(root, ts, rows, **extra):
    _write(os.path.join(root, f"battery_{ts}", "results.json"),
           {"results": rows, "total_tasks": len(rows), "graded": len(rows),
            "complete": True, **extra})


def _archive():
    root = tempfile.mkdtemp(prefix="battery_metrics_test_")
    _battery(root, "20260101_000000", [
        {"task_id": "a", "run_tag": "battery_a", "grade": "pass", "finished": True,
         "answer_text": "done", "auto_grade": "pass", "auto_evidence": "screen shows it"},
        {"task_id": "b", "run_tag": "battery_b", "grade": "fail", "finished": True,
         "answer_text": "done", "auto_grade": "fail", "auto_evidence": "nothing changed"},
        {"task_id": "c", "run_tag": "battery_c", "grade": "void", "finished": False,
         "answer_text": ""},
    ])
    _run_dir(root, "battery_a_20260101_000100",
            [{"wall_time_s": 4.0, "action": {"actions": []}},
             {"wall_time_s": 6.0, "action": {"actions": []},
              "verification": {"satisfied": True, "evidence": "e", "wall_time_s": 0.2,
                               "usage": {}}}])
    _run_dir(root, "battery_b_20260101_000200",
            [{"wall_time_s": 5.0, "action": {"actions": [],
                                             "warnings": ["guard_refusal: region tile diff 70.5"]}},
             {"wall_time_s": 8.0, "action": {"actions": []},
              "verification": {"satisfied": False, "evidence": "nothing changed",
                               "wall_time_s": 0.3, "usage": {}}}])
    _run_dir(root, "battery_c_20260101_000300",
            [{"wall_time_s": 3.0, "action": {"actions": []}}] * 20,
            summary_extra={"note": "max_steps reached"})
    return root


def test_completion_rate_excludes_voids_from_denominator():
    root = _archive()
    try:
        analysis = bm.analyze_battery(os.path.join(root, "battery_20260101_000000"), root)
        report = bm.aggregate([analysis])
        c = report["completion_rate"]
        assert c == {"passes": 1, "denominator": 2, "voids": 1,
                     "recorded_grades": 3, "complete": True, "pct": 50.0}
    finally:
        shutil.rmtree(root)


def test_false_finished_rate_is_confident_wrong_progress():
    """task b: finished=True (the model claimed done) but graded fail -- exactly the
    confident-wrong-progress case roadmap Phase 2 exists to catch."""
    root = _archive()
    try:
        analysis = bm.analyze_battery(os.path.join(root, "battery_20260101_000000"), root)
        report = bm.aggregate([analysis])
        ff = report["false_finished"]
        assert ff["count"] == 1 and ff["tasks"] == ["b"]
        assert ff["of_finished"] == 2   # a and b both finished=True; c did not
        assert ff["rate_pct"] == 50.0
    finally:
        shutil.rmtree(root)


def test_verifier_agreement_and_false_refusal_and_confirmation():
    """a: human pass, oracle pass -> agree. b: human fail, oracle fail -> agree. Neither
    is a false-refusal (oracle wrongly fails a true pass) or false-confirmation (oracle
    wrongly passes a true fail) here -- add one of each to prove both are distinguished."""
    root = _archive()
    try:
        # extra battery: d = human pass but oracle said fail (false refusal),
        #                e = human fail but oracle said pass (false confirmation)
        _battery(root, "20260102_000000", [
            {"task_id": "d", "run_tag": "battery_d", "grade": "pass", "finished": True,
             "answer_text": "done", "auto_grade": "fail", "auto_evidence": "e"},
            {"task_id": "e", "run_tag": "battery_e", "grade": "fail", "finished": True,
             "answer_text": "done", "auto_grade": "pass", "auto_evidence": "e"},
        ])
        _run_dir(root, "battery_d_20260102_000100", [{"wall_time_s": 5.0, "action": {}}])
        _run_dir(root, "battery_e_20260102_000100", [{"wall_time_s": 5.0, "action": {}}])

        a1 = bm.analyze_battery(os.path.join(root, "battery_20260101_000000"), root)
        a2 = bm.analyze_battery(os.path.join(root, "battery_20260102_000000"), root)
        report = bm.aggregate([a1, a2])
        v = report["verifier"]
        assert v["n_compared"] == 4   # a, b, d, e all have auto_grade + pass/fail grade
        assert v["agreement_pct"] == 50.0   # a and b agree, d and e don't
        assert v["false_refusals"]["count"] == 1 and v["false_refusals"]["tasks"] == ["d"]
        assert v["false_confirmations"]["count"] == 1 and v["false_confirmations"]["tasks"] == ["e"]
        assert v["basis"] == "human ground-truth sample only"
    finally:
        shutil.rmtree(root)


def test_completion_rate_keeps_missing_tasks_in_denominator():
    """An interrupted ten-task battery with one pass must read 1/10, never 1/1."""
    root = tempfile.mkdtemp(prefix="battery_metrics_test_")
    try:
        _battery(root, "20260101_000000", [
            {"task_id": "a", "run_tag": "battery_a", "grade": "pass",
             "finished": True, "answer_text": "done"},
        ], total_tasks=10, graded=1, complete=False)
        _run_dir(root, "battery_a_20260101_000100",
                 [{"wall_time_s": 5.0, "action": {}}])
        analysis = bm.analyze_battery(os.path.join(root, "battery_20260101_000000"), root)
        c = bm.aggregate([analysis])["completion_rate"]
        assert c["passes"] == 1 and c["denominator"] == 10 and c["pct"] == 10.0
        assert c["recorded_grades"] == 1 and c["complete"] is False
    finally:
        shutil.rmtree(root)


def test_d_c_verifier_metrics_use_only_human_spot_check_sample():
    root = tempfile.mkdtemp(prefix="battery_metrics_test_")
    try:
        _battery(root, "20260101_000000", [
            {"task_id": "sampled", "run_tag": "battery_sampled", "grade": "pass",
             "grader": "verifier", "human_grade": "fail", "auto_grade": "pass",
             "finished": True},
            {"task_id": "not_sampled", "run_tag": "battery_not_sampled", "grade": "pass",
             "grader": "verifier", "human_grade": None, "auto_grade": "pass",
             "finished": True},
        ])
        _run_dir(root, "battery_sampled_20260101_000100", [])
        _run_dir(root, "battery_not_sampled_20260101_000200", [])
        analysis = bm.analyze_battery(os.path.join(root, "battery_20260101_000000"), root)
        v = bm.aggregate([analysis])["verifier"]
        assert v["n_compared"] == 1
        assert v["false_confirmations"]["count"] == 1
        assert v["false_confirmations"]["tasks"] == ["sampled"]
    finally:
        shutil.rmtree(root)


def test_verifier_is_none_when_no_battery_ran_shadow():
    root = tempfile.mkdtemp(prefix="battery_metrics_test_")
    try:
        _battery(root, "20260101_000000", [
            {"task_id": "a", "run_tag": "battery_a", "grade": "pass", "finished": True,
             "answer_text": "done"}])   # no auto_grade key at all -- verify_mode="off"
        _run_dir(root, "battery_a_20260101_000100", [{"wall_time_s": 5.0, "action": {}}])
        analysis = bm.analyze_battery(os.path.join(root, "battery_20260101_000000"), root)
        report = bm.aggregate([analysis])
        assert report["verifier"] is None
    finally:
        shutil.rmtree(root)


def test_guard_refusal_rate_scans_step_warnings():
    root = _archive()
    try:
        analysis = bm.analyze_battery(os.path.join(root, "battery_20260101_000000"), root)
        report = bm.aggregate([analysis])
        g = report["guard_refusal_rate"]
        # a: 2 steps, 0 refusals. b: 2 steps, 1 refusal. c: 20 steps, 0 refusals.
        assert g == {"steps_with_refusal": 1, "total_steps": 24, "pct": 4.2}
    finally:
        shutil.rmtree(root)


def test_endings_split_budget_exhaustion_from_answered():
    root = _archive()
    try:
        analysis = bm.analyze_battery(os.path.join(root, "battery_20260101_000000"), root)
        report = bm.aggregate([analysis])
        endings = report["endings"]
        assert endings["answered"]["count"] == 2          # a, b both finished=True
        assert endings["budget_exhaustion"]["count"] == 1  # c hit max_steps
        assert {t["task_id"] for t in endings["budget_exhaustion"]["tasks"]} == {"c"}
    finally:
        shutil.rmtree(root)


def test_honest_refusal_heuristic_is_labeled_not_authoritative():
    bucket, detail = bm._classify_ending(
        {"finished": True, "answer_text": "I was unable to find a paint application"},
        {})
    assert bucket == "honest_refusal_heuristic"
    assert "unable" in detail


def test_verify_and_actor_latency_are_separate_distributions():
    """holo3.1 serves with --parallel 1 (kvm_agent/llm/serving.py) -- a verify call
    serializes behind the actor call. The combined figure is what a shadow-verified
    step actually costs end to end."""
    root = _archive()
    try:
        analysis = bm.analyze_battery(os.path.join(root, "battery_20260101_000000"), root)
        report = bm.aggregate([analysis])
        lat = report["latency_s"]
        assert lat["actor_per_step"]["n"] == 24   # 2+2+20 steps total
        assert lat["verify_per_step"]["n"] == 2    # only a's and b's finished steps
        assert lat["verified_step_combined"]["n"] == 2
        # a's finished step: 6.0 (actor) + 0.2 (verify) = 6.2
        assert lat["verified_step_combined"]["max"] == 8.3   # b: 8.0 + 0.3
    finally:
        shutil.rmtree(root)


def test_skips_pre_results_json_battery_dirs_without_crashing():
    """Pre-2026-07-21 battery dirs wrote battery_summary.json, not results.json --
    must be skipped and reported, never silently counted as zero-row data."""
    root = _archive()
    try:
        legacy = os.path.join(root, "battery_20260101_010000")
        os.makedirs(legacy)
        _write(os.path.join(legacy, "battery_summary.json"), {"old": "shape"})
        assert bm.analyze_battery(legacy, root) is None
    finally:
        shutil.rmtree(root)


def test_find_battery_dirs_ignores_per_task_run_dirs():
    """battery_<task_id>_<ts> (per-task RunRecorder dirs) must never be mistaken for
    battery_<ts> (results-summary) dirs -- they don't match \\d{8}_\\d{6} right after
    'battery_'."""
    root = _archive()
    try:
        found = bm.find_battery_dirs(root)
        assert found == ["battery_20260101_000000"]
        assert "battery_a_20260101_000100" not in found
    finally:
        shutil.rmtree(root)


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
