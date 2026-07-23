"""Offline checks for the one physical model/harness calibration.

No model server, camera, HID appliance, browser, or page server is contacted.
"""
import json

import cv2
import numpy as np

from tools import physical_calibration_smoke as smoke


WIDTH = 1280
HEIGHT = 720


def _frame(stage, rejected=False):
    frame = np.full((HEIGHT, WIDTH, 3), (244, 244, 244), dtype=np.uint8)
    # Fixed magenta page border, expressed in BGR.
    cv2.rectangle(frame, (0, 0), (WIDTH - 1, HEIGHT - 1), (212, 0, 255), 16)
    if stage == "start":
        cv2.rectangle(frame, (180, 300), (540, 410), (232, 103, 18), -1)
    elif stage == "entry":
        cv2.rectangle(frame, (17, 17), (WIDTH - 18, HEIGHT - 18), (77, 184, 240), -1)
        cv2.rectangle(frame, (470, 530), (810, 640), (232, 103, 18), -1)
        if rejected:
            cv2.rectangle(frame, (380, 440), (900, 490), (28, 28, 185), -1)
    elif stage == "success":
        cv2.rectangle(frame, (17, 17), (WIDTH - 18, HEIGHT - 18), (104, 185, 25), -1)
    return frame


def _write_step(actor_dir, index, frame, actions, error=None, executed=True):
    ok, png = cv2.imencode(".png", frame)
    assert ok
    (actor_dir / f"step_{index:02d}.png").write_bytes(png.tobytes())
    parsed = {"actions": actions, "note": None, "thought": "fixture"}
    if error:
        parsed["error"] = error
    (actor_dir / f"step_{index:02d}.json").write_text(json.dumps({
        "step": index, "message": {}, "action": parsed, "usage": {},
        "wall_time_s": 0.1, "executed": executed, "verification": None,
    }))


def test_seeded_page_is_reproducible_and_has_no_result_channel():
    first = smoke.calibration_spec(7319)
    second = smoke.calibration_spec(7319)
    assert first == second
    page = smoke.render_page(first)
    assert first["nonce"] in page
    assert f"left: {first['button_left_pct']}%" in page
    assert f"top: {first['button_top_pct']}%" in page
    assert 'field.value === expected' in page
    assert "CALIBRATION SUCCESS" in page
    assert all(token not in page for token in (
        "fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon"))


def test_camera_oracle_distinguishes_all_page_stages_and_target_bounds():
    absent = smoke.visual_oracle(np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8))
    start = smoke.visual_oracle(_frame("start"))
    entry = smoke.visual_oracle(_frame("entry", rejected=True))
    success = smoke.visual_oracle(_frame("success"))

    assert absent["stage"] == "absent" and not absent["page_visible"]
    assert start["stage"] == "start" and start["action_target_bounds"]
    assert smoke._inside(start["action_target_bounds"], [300, 350]) is True
    assert smoke._inside(start["action_target_bounds"], [900, 350]) is False
    assert entry["stage"] == "entry" and entry["input_rejected"]
    assert smoke._inside(entry["action_target_bounds"], [350, 400]) is False
    assert smoke._inside(entry["action_target_bounds"], [640, 585]) is True
    assert success["stage"] == "success" and success["success"]
    assert success["action_target_bounds"] is None


def test_actor_evidence_requires_finished_to_see_success(tmp_path):
    actor = tmp_path / "physical_calibration_actor_fixture"
    actor.mkdir()
    _write_step(actor, 0, _frame("start"), [
        {"action": "left_click", "coordinate": [300, 350]}])
    _write_step(actor, 1, _frame("entry"), [
        {"action": "left_click", "coordinate": [350, 400]},
        {"action": "type", "text": "KVM-7319", "press_enter": False},
        {"action": "left_click", "coordinate": [640, 585]}])
    _write_step(actor, 2, _frame("success"), [
        {"action": "finished", "text": "done"}])

    evidence = smoke.inspect_actor_run(actor)
    assert evidence["complete_evidence"]
    assert [step["stage"] for step in evidence["steps"]] == [
        "start", "entry", "success"]
    assert evidence["finished_claim"]["decision_frame_success"] is True
    verdict = smoke.classify_result(
        {"finished": True}, smoke.visual_oracle(_frame("success")),
        evidence, "KVM-7319")
    assert verdict["status"] == "pass"


def test_submit_plus_finished_without_reobservation_is_protocol_failure(tmp_path):
    actor = tmp_path / "physical_calibration_actor_fixture"
    actor.mkdir()
    _write_step(actor, 0, _frame("entry"), [
        {"action": "type", "text": "KVM-7319", "press_enter": False},
        {"action": "left_click", "coordinate": [640, 585]},
        {"action": "finished", "text": "done"}])
    evidence = smoke.inspect_actor_run(actor)
    verdict = smoke.classify_result(
        {"finished": True}, smoke.visual_oracle(_frame("success")),
        evidence, "KVM-7319")
    assert verdict["status"] == "calibration_failure"
    assert verdict["first_broken_boundary"] == "termination_protocol"


def test_failure_classification_uses_recorded_boundary_evidence(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    _write_step(outside, 0, _frame("start"), [
        {"action": "left_click", "coordinate": [900, 350]}])
    coordinate = smoke.classify_result(
        {"finished": False}, smoke.visual_oracle(_frame("start")),
        smoke.inspect_actor_run(outside), "KVM-7319")
    assert coordinate["first_broken_boundary"] == "coordinate"

    inside = tmp_path / "inside"
    inside.mkdir()
    _write_step(inside, 0, _frame("start"), [
        {"action": "left_click", "coordinate": [300, 350]}])
    hid = smoke.classify_result(
        {"finished": False}, smoke.visual_oracle(_frame("start")),
        smoke.inspect_actor_run(inside), "KVM-7319")
    assert hid["first_broken_boundary"] == "hid"

    parse = tmp_path / "parse"
    parse.mkdir()
    _write_step(parse, 0, _frame("start"), [],
                error="bad_content_json", executed=False)
    rejected = smoke.classify_result(
        {"finished": False}, smoke.visual_oracle(_frame("start")),
        smoke.inspect_actor_run(parse), "KVM-7319")
    assert rejected["first_broken_boundary"] == "request_parse"
