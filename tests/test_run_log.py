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


def test_step_keeps_tool_results_hid_responses_and_run_owned_request_path():
    with tempfile.TemporaryDirectory() as td:
        rec = _recorder(td)
        assert rec.request_log_path == os.path.join(rec.dir, "model_requests.jsonl")
        rec.log_step(
            0, b"png", {"content": "{}"},
            {"actions": [{"action": "left_click"}]}, {}, 0.1,
            tool_results=[("click_desktop", "Executed. Screen changed.")],
            hid_events=[{
                "path": "/hid/click", "ok": True,
                "response": {"ack": "C", "wire": {"kbd_online": True}},
            }])
        with open(os.path.join(rec.dir, "step_00.json")) as f:
            step = json.load(f)
    assert step["tool_results"] == [
        {"tool": "click_desktop", "text": "Executed. Screen changed."}
    ]
    assert step["hid_events"][0]["response"]["wire"]["kbd_online"] is True


# --- roadmap Phase 2 slice D-b: verification threading ---
def test_summary_verifications_default_to_none_when_absent():
    """The overwhelming majority of steps carry no verification at all (D-b only
    verifies a `finished` claim) -- verifications must be an explicit None per step,
    not an absent key, so a consumer can zip it against `actions` positionally."""
    with tempfile.TemporaryDirectory() as td:
        rec = _recorder(td)
        rec.log_step(0, b"png", {}, {"actions": [{"action": "left_click"}]}, {}, 0.1)
        rec.log_step(1, b"png", {}, {"actions": [{"action": "finished"}]}, {}, 0.1)
        rec.finish(True, note="done")
        with open(os.path.join(rec.dir, "summary.json")) as f:
            summary = json.load(f)
    assert summary["verifications"] == [None, None]
    assert summary["verified_finish"] is None


def test_summary_verified_finish_pulls_the_verdict_off_the_right_step():
    verdict = {"satisfied": True, "evidence": "calculator shows 56",
              "wall_time_s": 0.05, "usage": {"prompt_tokens": 7}}
    with tempfile.TemporaryDirectory() as td:
        rec = _recorder(td)
        rec.log_step(0, b"png", {}, {"actions": [{"action": "left_click"}]}, {}, 0.1)
        rec.log_step(1, b"png", {}, {"actions": [{"action": "finished"}]}, {}, 0.1,
                    verification=verdict)
        rec.finish(True, note="56")
        with open(os.path.join(rec.dir, "summary.json")) as f:
            summary = json.load(f)
    assert summary["verifications"] == [None, verdict], \
        "verifications is parallel to actions/steps, not just the one that mattered"
    assert summary["verified_finish"] == verdict


def test_summary_verified_finish_is_none_on_a_dropped_run_with_no_claim():
    """A run that aborts (stuck limit, no-progress, max_steps) never produces a
    finished claim, so verified_finish must be None even though verify_mode was active
    for the whole run -- absence of a claim is not the same as an unsatisfied one."""
    with tempfile.TemporaryDirectory() as td:
        rec = _recorder(td)
        rec.log_step(0, b"png", {}, {"actions": [], "error": "bad_content_json"}, {},
                    0.1, executed=False)
        rec.finish(False, note="stuck limit hit")
        with open(os.path.join(rec.dir, "summary.json")) as f:
            summary = json.load(f)
    assert summary["verifications"] == [None]
    assert summary["verified_finish"] is None


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
