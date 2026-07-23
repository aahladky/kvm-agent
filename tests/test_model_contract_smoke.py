"""Offline tests for tools/model_contract_smoke.py.

No model server, camera, or HID is contacted. Live endpoint coverage is the explicit
tool invocation recorded under runs/.
"""
import json

import numpy as np
import pytest

from kvm_agent.models.base import StepDecision
from tools import model_contract_smoke as smoke


def _decision(tool_calls, actions, error=None):
    content = json.dumps({
        "note": None,
        "thought": "fixture",
        "tool_calls": tool_calls,
    })
    step = {"actions": actions, "note": None, "thought": "fixture"}
    if error:
        step["error"] = error
    return StepDecision(
        step=step,
        message={"role": "assistant", "content": content},
        usage={"prompt_tokens": 1, "completion_tokens": 1},
        data_url="data:image/jpeg;base64,test",
        instruction="fixture",
    )


def _passing_decision(case_id):
    if case_id == "click_target":
        return _decision(
            [{"tool_name": "click_desktop", "x": 500, "y": 520}],
            [{"action": "left_click", "coordinate": [640.0, 374.4],
              "element": "CONTINUE"}],
        )
    if case_id == "type_nonce":
        return _decision(
            [{"tool_name": "write_desktop", "content": smoke.NONCE}],
            [{"action": "type", "text": smoke.NONCE, "press_enter": False}],
        )
    if case_id == "complete":
        return _decision(
            [{"tool_name": "answer", "content": "done"}],
            [{"action": "finished", "text": "done"}],
        )
    return _decision(
        [{"tool_name": "click_desktop", "x": 500, "y": 630}],
        [{"action": "left_click", "coordinate": [640.0, 453.6],
          "element": "CONTINUE"}],
    )


def test_rendered_cases_are_fixed_full_screen_frames():
    assert tuple(smoke.CASES) == smoke.CASE_ORDER
    for case_id in smoke.CASE_ORDER:
        first = smoke.render_case(case_id)
        second = smoke.render_case(case_id)
        assert first.shape == (smoke.HEIGHT, smoke.WIDTH, 3)
        assert first.dtype == np.uint8
        assert np.array_equal(first, second), f"{case_id} frame drifted within one run"


def test_each_acceptance_predicate_passes_only_its_broad_contract():
    for case_id in smoke.CASE_ORDER:
        verdict = smoke.evaluate_case(case_id, _passing_decision(case_id))
        assert verdict["passed"], (case_id, verdict)

    outside = _decision(
        [{"tool_name": "click_desktop", "x": 50, "y": 50}],
        [{"action": "left_click", "coordinate": [64.0, 36.0]}],
    )
    assert not smoke.evaluate_case("click_target", outside)["passed"]

    wrong_text = _decision(
        [{"tool_name": "write_desktop", "content": "wrong"}],
        [{"action": "type", "text": "wrong", "press_enter": False}],
    )
    assert not smoke.evaluate_case("type_nonce", wrong_text)["passed"]

    early_finish = _decision(
        [{"tool_name": "answer", "content": "done"}],
        [{"action": "finished", "text": "done"}],
    )
    assert not smoke.evaluate_case("incomplete", early_finish)["passed"]


def test_click_case_records_raw_to_pixel_projection():
    verdict = smoke.evaluate_case(
        "click_target", _passing_decision("click_target"))
    transform = verdict["transformations"][0]
    assert transform["raw_normalized"] == [500, 520]
    assert transform["expected_pixels"] == pytest.approx([640.0, 374.4])
    assert transform["parsed_pixels"] == pytest.approx([640.0, 374.4])
    assert transform["projection_matches"] is True


def test_smoke_writes_complete_artifacts_with_injected_decisions(tmp_path):
    def decide_fn(**kwargs):
        return _passing_decision(kwargs["case_id"])

    snapshot = {
        "endpoint": "http://test/v1",
        "reachable": True,
        "configured": True,
        "resident": True,
        "params": {"has_mmproj": True},
    }
    code = smoke.run_smoke(
        tmp_path, list(smoke.CASE_ORDER), 5.0, snapshot, decide_fn=decide_fn)
    assert code == 0
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["status"] == "pass"
    assert summary["complete"] is True
    assert summary["passed"] == 4 and summary["failed"] == 0
    assert (tmp_path / "meta.json").exists()
    assert (tmp_path / "system_prompt.txt").exists()
    for case_id in smoke.CASE_ORDER:
        case_dir = tmp_path / case_id
        assert (case_dir / "frame.jpg").exists()
        assert (case_dir / "request.json").exists()
        assert (case_dir / "raw_response.json").exists()
        result = json.loads((case_dir / "result.json").read_text())
        assert result["status"] == "pass"
        assert result["parsed_step"]["actions"]


def test_infrastructure_error_stops_without_laundering_unattempted_cases(tmp_path):
    calls = []

    def decide_fn(**kwargs):
        calls.append(kwargs["case_id"])
        raise TimeoutError("fixture timeout")

    snapshot = {
        "endpoint": "http://test/v1",
        "reachable": True,
        "configured": True,
        "resident": True,
        "params": {"has_mmproj": True},
    }
    code = smoke.run_smoke(
        tmp_path, list(smoke.CASE_ORDER), 5.0, snapshot, decide_fn=decide_fn)
    assert code == 2
    assert calls == ["click_target"]
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["status"] == "infrastructure_error"
    assert summary["complete"] is False
    assert summary["completed_cases"] == ["click_target"]
    assert summary["passed"] == 0 and summary["failed"] == 1


def test_preflight_failure_makes_no_model_calls(tmp_path):
    calls = []
    snapshot = {
        "endpoint": "http://test/v1",
        "reachable": False,
        "configured": None,
        "resident": None,
        "params": {},
        "error": "connection refused",
    }
    code = smoke.run_smoke(
        tmp_path, ["complete"], 5.0, snapshot,
        decide_fn=lambda **kwargs: calls.append(kwargs))
    assert code == 2
    assert calls == []
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["status"] == "infrastructure_error"
    assert summary["complete"] is False
    assert summary["completed_cases"] == []
