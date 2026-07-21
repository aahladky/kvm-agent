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
