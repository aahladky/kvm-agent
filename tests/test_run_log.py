"""
test_run_log.py — OFFLINE test for RunRecorder's summary (2026-07-21 second review
#6: summary.json's "actions" list was all-None on every post-rearchitecture run --
it read s["action"].get("action") off the batched step shape, whose actions live
under "actions").

    python tests/test_run_log.py
"""
import sys, os, json, tempfile, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kvm_agent.instrumentation.run_log as run_log


def _recorder(td, tag="t"):
    real_cfg = run_log.CFG
    run_log.CFG = types.SimpleNamespace(runs_dir=td)
    try:
        return run_log.RunRecorder(tag, "test goal")
    finally:
        run_log.CFG = real_cfg


def test_summary_actions_from_batched_step_shape():
    with tempfile.TemporaryDirectory() as td:
        rec = _recorder(td)
        step = {"actions": [{"action": "left_click", "coordinate": [1, 2]},
                            {"action": "finished", "text": "done"}],
                "note": None}
        rec.log_step(0, b"png", {"content": "{}"}, step, {"prompt_tokens": 1}, 0.5)
        rec.finish(True, note="done")
        with open(os.path.join(rec.dir, "summary.json")) as f:
            summary = json.load(f)
    assert summary["actions"] == [["left_click", "finished"]], \
        f"per-step action kinds extracted from the batched shape, got {summary['actions']}"
    assert summary["final_action"] == ["left_click", "finished"], \
        "final_action is the last step's kind list"


def test_summary_actions_legacy_and_empty():
    with tempfile.TemporaryDirectory() as td:
        rec = _recorder(td)
        rec.log_step(0, b"png", {}, {"action": "left_click", "coordinate": [1, 2]}, {}, 0.1)
        rec.log_step(1, b"png", {}, {"actions": [], "error": "bad_content_json"}, {}, 0.1)
        rec.finish(False)
        with open(os.path.join(rec.dir, "summary.json")) as f:
            summary = json.load(f)
    assert summary["actions"] == [["left_click"], []], \
        "legacy single-action dicts and error steps both read correctly"
    with tempfile.TemporaryDirectory() as td2:
        rec2 = _recorder(td2)
        rec2.finish(False)
        with open(os.path.join(rec2.dir, "summary.json")) as f:
            empty = json.load(f)
    assert empty["actions"] == [] and empty["final_action"] is None, \
        "zero-step run summarizes cleanly"


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
