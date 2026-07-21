"""
test_battery.py — OFFLINE test for the battery runner's pure parts (task loading,
grading input, results writing). The interactive runner itself is live-verified.

    python tests/test_battery.py   (or pytest tests/test_battery.py)
"""
import sys, os, json, tempfile, builtins
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import battery


def test_load_tasks_and_defaults():
    with tempfile.TemporaryDirectory() as td:
        good = os.path.join(td, "tasks.json")
        with open(good, "w") as f:
            json.dump([{"id": "t1", "instruction": "do thing"}], f)
        tasks = battery.load_tasks(good)
        assert len(tasks) == 1 and tasks[0]["id"] == "t1", "load_tasks returns tasks"
        assert tasks[0]["max_steps"] == 15, "max_steps defaults to 15"


def test_task_without_id_rejected():
    with tempfile.TemporaryDirectory() as td:
        bad = os.path.join(td, "bad.json")
        with open(bad, "w") as f:
            json.dump([{"instruction": "no id"}], f)
        try:
            battery.load_tasks(bad)
            assert False, "task without id must be rejected"
        except AssertionError as e:
            if "must be rejected" in str(e):
                raise


def test_write_results_roundtrips():
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "results.json")
        battery.write_results(out, {"results": [], "score": "0/0"})
        with open(out) as f:
            assert json.load(f)["score"] == "0/0"


def _grade_with_answers(answers, task, result):
    it = iter(answers)
    real_input = builtins.input
    builtins.input = lambda prompt="": next(it)
    try:
        return battery.grade_task(task, result)
    finally:
        builtins.input = real_input


def test_grade_reasks_on_empty_then_passes():
    # a grade can never be silently recorded (finding #8): empty input re-asks
    v = _grade_with_answers(["", "p looks good"], {"id": "t1"},
                            {"finished": True, "answer_text": ""})
    assert v == {"grade": "pass", "note": "looks good"}


def test_grade_fail_with_note():
    v = _grade_with_answers(["f fell over"], {"id": "t2"},
                            {"finished": False, "answer_text": ""})
    assert v == {"grade": "fail", "note": "fell over"}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
