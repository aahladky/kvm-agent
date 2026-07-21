"""
test_battery.py — OFFLINE test for the battery runner's pure parts (task loading,
grading input, results writing). The interactive runner itself is live-verified.

    python tests/test_battery.py
"""
import sys, os, json, tempfile, builtins
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import battery

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)

with tempfile.TemporaryDirectory() as td:
    good = os.path.join(td, "tasks.json")
    with open(good, "w") as f:
        json.dump([{"id": "t1", "instruction": "do thing"}], f)
    tasks = battery.load_tasks(good)
    check("load_tasks returns tasks", len(tasks) == 1 and tasks[0]["id"] == "t1")
    check("max_steps defaults to 15", tasks[0]["max_steps"] == 15)

    bad = os.path.join(td, "bad.json")
    with open(bad, "w") as f:
        json.dump([{"instruction": "no id"}], f)
    try:
        battery.load_tasks(bad)
        check("task without id rejected", False)
    except AssertionError:
        check("task without id rejected", True)

    out = os.path.join(td, "results.json")
    battery.write_results(out, {"results": [], "score": "0/0"})
    with open(out) as f:
        check("write_results round-trips", json.load(f)["score"] == "0/0")

# grading: empty input re-asks (a grade can never be silently recorded, finding #8);
# 'p note' -> pass with note; 'f' -> fail with empty note
answers = iter(["", "p looks good"])
real_input = builtins.input
builtins.input = lambda prompt="": next(answers)
try:
    v = battery.grade_task({"id": "t1"}, {"finished": True, "answer_text": ""})
finally:
    builtins.input = real_input
check("grade_task re-asks on empty, then passes", v == {"grade": "pass", "note": "looks good"})

answers = iter(["f fell over"])
builtins.input = lambda prompt="": next(answers)
try:
    v = battery.grade_task({"id": "t2"}, {"finished": False, "answer_text": ""})
finally:
    builtins.input = real_input
check("grade_task fail with note", v == {"grade": "fail", "note": "fell over"})

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
