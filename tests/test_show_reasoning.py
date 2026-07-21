"""
test_show_reasoning.py — OFFLINE tests for tools/show_reasoning.py, the designated
first-responder on any failed run (2026-07-21 review P1-12: its repeat-detector
spoke a retired action vocabulary — 'key' — while the live loop emits hotkey/
double_click, and it read action.get("key") where the live field is "keys"; it
also missed the 2026-07-21 batched-step record shape entirely).

    python tests/test_show_reasoning.py
"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import show_reasoning as sr


def test_same_action_live_vocabulary():
    click = lambda x, y: {"action": "left_click", "coordinate": [x, y]}
    assert sr._same_action(click(100, 100), click(110, 110)), "click cluster within tol"
    assert not sr._same_action(click(100, 100), click(200, 200)), "distant clicks differ"
    assert sr._same_action({"action": "double_click", "coordinate": [100, 100]},
                           {"action": "double_click", "coordinate": [120, 120]}), \
        "double_click repeats detected"
    assert not sr._same_action(click(100, 100),
                               {"action": "double_click", "coordinate": [100, 100]}), \
        "kind must match"
    assert sr._same_action({"action": "hotkey", "keys": ["ctrl", "s"]},
                           {"action": "hotkey", "keys": ["ctrl", "s"]}), \
        "hotkey repeats detected (the live kind; 'key' is dead)"
    assert not sr._same_action({"action": "hotkey", "keys": ["ctrl", "s"]},
                               {"action": "hotkey", "keys": ["ctrl", "c"]})
    assert sr._same_action({"action": "type", "text": "abc"},
                           {"action": "type", "text": "abc"})
    assert not sr._same_action({"action": "type", "text": "abc"},
                               {"action": "type", "text": "abd"})
    assert sr._same_action({"action": "hold_and_tap", "hold_keys": ["shift"], "tap_keys": ["tab"]},
                           {"action": "hold_and_tap", "hold_keys": ["shift"], "tap_keys": ["tab"]})
    assert not sr._same_action({"action": "update_plan", "goals": []},
                               {"action": "update_plan", "goals": []}), \
        "plan updates are never 'repeats'"
    assert not sr._same_action({"action": "finished", "text": "x"},
                               {"action": "finished", "text": "x"})


def test_actions_of_batched_and_legacy_records():
    batched = {"action": {"actions": [{"action": "hotkey", "keys": ["win", "r"]}],
                          "note": None}}
    assert sr._actions_of(batched) == [{"action": "hotkey", "keys": ["win", "r"]}], \
        "current batched step record"
    legacy = {"action": {"action": "left_click", "coordinate": [1, 2]}}
    assert sr._actions_of(legacy) == [{"action": "left_click", "coordinate": [1, 2]}], \
        "pre-batch single-action record still reads"
    assert sr._actions_of({"action": {}}) == []


def test_show_flags_hotkey_repeat_and_prints_keys():
    with tempfile.TemporaryDirectory() as td:
        for i in range(2):
            rec = {"step": i, "message": {"reasoning_content": f"thinking {i}"},
                   "wall_time_s": 1.0,
                   "action": {"actions": [{"action": "hotkey", "keys": ["ctrl", "s"]}]}}
            with open(os.path.join(td, f"step_{i:02d}.json"), "w") as f:
                json.dump(rec, f)
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            sr.show(td)
        out = buf.getvalue()
    assert "REPEAT" in out, "a repeated hotkey is flagged"
    assert "['ctrl', 's']" in out, "hotkey steps print their keys list, not empty detail"
    assert "thinking 0" in out and "thinking 1" in out, "reasoning still prints"


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
