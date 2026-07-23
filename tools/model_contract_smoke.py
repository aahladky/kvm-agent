#!/usr/bin/env python3
"""Small, controlled real-model smoke for the production Holo session seam.

This is not an agent loop and never touches the physical rig. Four generated frames
exercise click projection, typing, correct completion, and refusal to finish early
through the real local endpoint and production HoloSession/parser.

    python tools/model_contract_smoke.py
    python tools/model_contract_smoke.py --case click_target

Every invocation is noninteractive and writes one self-contained directory:
    runs/model_contract_smoke_<YYYYMMDD_HHMMSS>/
"""
from __future__ import annotations

import argparse
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.config import CFG
from kvm_agent.llm.serving import serving_snapshot
import kvm_agent.models.holo as holo
from kvm_agent.models.base import StepDecision


WIDTH = 1280
HEIGHT = 720
NONCE = "KVM-7319"
CASE_ORDER = ("click_target", "type_nonce", "complete", "incomplete")

CASES = {
    "click_target": {
        "instruction": "Click the large blue CONTINUE button.",
        "predicate": "click_inside_target",
        "target_rect": [470, 320, 810, 430],
    },
    "type_nonce": {
        "instruction": (
            f"The verification-code field is already focused. Type {NONCE} into it "
            "without pressing Enter."
        ),
        "predicate": "type_nonce",
        "nonce": NONCE,
    },
    "complete": {
        "instruction": (
            "The task is complete only when the screen visibly shows SUCCESS. "
            "Report completion if it is complete."
        ),
        "predicate": "finished",
    },
    "incomplete": {
        "instruction": (
            "Complete the setup. Do not report completion until the screen shows "
            "SUCCESS."
        ),
        "predicate": "valid_non_finished_action",
        "target_rect": [470, 405, 810, 500],
    },
}


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def _centered_text(frame, text, center_x, baseline_y, scale=1.0,
                   color=(32, 32, 32), thickness=2):
    font = cv2.FONT_HERSHEY_SIMPLEX
    (width, _), _ = cv2.getTextSize(text, font, scale, thickness)
    cv2.putText(frame, text, (int(center_x - width / 2), baseline_y), font, scale,
                color, thickness, cv2.LINE_AA)


def render_case(case_id: str) -> np.ndarray:
    """Generate one fixed 1280x720 desktop-like frame."""
    if case_id not in CASES:
        raise ValueError(f"unknown case {case_id!r}")

    frame = np.full((HEIGHT, WIDTH, 3), (41, 44, 51), dtype=np.uint8)
    # Minimal GNOME-like top bar and a centered application window.
    cv2.rectangle(frame, (0, 0), (WIDTH - 1, 36), (27, 28, 31), -1)
    cv2.putText(frame, "Activities", (18, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (235, 235, 235), 1, cv2.LINE_AA)
    _centered_text(frame, "Model Integration Calibration", WIDTH // 2, 25, 0.55,
                   (235, 235, 235), 1)
    cv2.rectangle(frame, (140, 85), (1140, 650), (247, 247, 247), -1)
    cv2.rectangle(frame, (140, 85), (1140, 135), (224, 226, 230), -1)
    cv2.putText(frame, "Calibration", (165, 118), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (40, 40, 40), 2, cv2.LINE_AA)

    if case_id == "click_target":
        _centered_text(frame, "Ready to continue", WIDTH // 2, 230, 1.15)
        _centered_text(frame, "Select the highlighted action below.", WIDTH // 2,
                       275, 0.65, (85, 85, 85), 1)
        x1, y1, x2, y2 = CASES[case_id]["target_rect"]
        cv2.rectangle(frame, (x1, y1), (x2, y2), (214, 103, 38), -1)
        _centered_text(frame, "CONTINUE", WIDTH // 2, 390, 1.05,
                       (255, 255, 255), 2)
        cv2.rectangle(frame, (245, 510), (405, 565), (215, 215, 215), -1)
        _centered_text(frame, "Cancel", 325, 546, 0.65, (80, 80, 80), 1)

    elif case_id == "type_nonce":
        _centered_text(frame, "Enter verification code", WIDTH // 2, 225, 1.0)
        _centered_text(frame, f"Code shown:  {NONCE}", WIDTH // 2, 290, 0.85,
                       (35, 35, 35), 2)
        cv2.rectangle(frame, (355, 345), (925, 430), (255, 255, 255), -1)
        cv2.rectangle(frame, (355, 345), (925, 430), (214, 103, 38), 4)
        cv2.line(frame, (385, 365), (385, 410), (35, 35, 35), 3)
        _centered_text(frame, "Field focused - type the code now", WIDTH // 2,
                       495, 0.62, (75, 75, 75), 1)

    elif case_id == "complete":
        cv2.rectangle(frame, (315, 215), (965, 510), (76, 157, 86), -1)
        _centered_text(frame, "SUCCESS", WIDTH // 2, 345, 1.8,
                       (255, 255, 255), 4)
        _centered_text(frame, "Calibration task complete", WIDTH // 2, 410, 0.85,
                       (255, 255, 255), 2)
        _centered_text(frame, "No further action is required", WIDTH // 2, 455, 0.65,
                       (245, 245, 245), 1)

    else:  # incomplete
        _centered_text(frame, "SETUP NOT COMPLETE", WIDTH // 2, 225, 1.05,
                       (30, 90, 190), 3)
        _centered_text(frame, "Step 1 of 2", WIDTH // 2, 285, 0.8,
                       (70, 70, 70), 2)
        _centered_text(frame, "Continue to the final confirmation screen.",
                       WIDTH // 2, 345, 0.65, (80, 80, 80), 1)
        x1, y1, x2, y2 = CASES[case_id]["target_rect"]
        cv2.rectangle(frame, (x1, y1), (x2, y2), (214, 103, 38), -1)
        _centered_text(frame, "CONTINUE", WIDTH // 2, 466, 0.95,
                       (255, 255, 255), 2)

    return frame


def _jpeg_bytes(frame: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise RuntimeError("OpenCV failed to encode generated contract frame")
    return encoded.tobytes()


def _inside(rect, coordinate) -> bool:
    if not isinstance(coordinate, (list, tuple)) or len(coordinate) != 2:
        return False
    x1, y1, x2, y2 = rect
    x, y = coordinate
    return x1 <= x <= x2 and y1 <= y <= y2


def _transform_evidence(decision: StepDecision) -> list[dict]:
    """Pair raw normalized pointer values with the production parser's pixels."""
    try:
        raw_calls = json.loads(decision.message.get("content") or "{}").get(
            "tool_calls", [])
    except (AttributeError, json.JSONDecodeError):
        return []
    raw_pointer = [
        call for call in raw_calls
        if call.get("tool_name") in {
            "click_desktop", "double_click_desktop", "scroll_desktop",
            "drag_to_desktop", "move_to_desktop",
        } and "x" in call and "y" in call
    ]
    parsed_pointer = [
        action for action in decision.actions
        if action.get("action") in {
            "left_click", "double_click", "scroll", "drag_to", "move_to",
        } and action.get("coordinate") is not None
    ]
    evidence = []
    for raw, parsed in zip(raw_pointer, parsed_pointer):
        raw_x, raw_y = raw["x"], raw["y"]
        expected = None
        if isinstance(raw_x, (int, float)) and isinstance(raw_y, (int, float)):
            expected = holo.project_point(raw_x, raw_y, WIDTH, HEIGHT)
        actual = parsed.get("coordinate")
        evidence.append({
            "tool_name": raw.get("tool_name"),
            "raw_normalized": [raw_x, raw_y],
            "expected_pixels": expected,
            "parsed_pixels": actual,
            "projection_matches": (
                expected is not None
                and len(actual or ()) == 2
                and all(abs(float(a) - float(b)) < 1e-6
                        for a, b in zip(actual, expected))
            ),
        })
    return evidence


def evaluate_case(case_id: str, decision: StepDecision) -> dict:
    """Evaluate broad action invariants, never exact model prose."""
    spec = CASES[case_id]
    actions = decision.actions
    transforms = _transform_evidence(decision)
    if decision.error:
        return {"passed": False, "reason": f"parsed response error: {decision.error}",
                "transformations": transforms}
    if not actions:
        return {"passed": False, "reason": "no parsed actions",
                "transformations": transforms}

    predicate = spec["predicate"]
    if predicate == "click_inside_target":
        matches = [
            action for action in actions
            if action.get("action") in ("left_click", "double_click")
            and _inside(spec["target_rect"], action.get("coordinate"))
        ]
        passed = bool(matches)
        reason = ("target click present" if passed
                  else f"no click inside target rectangle {spec['target_rect']}")
    elif predicate == "type_nonce":
        matches = [
            action for action in actions
            if action.get("action") == "type"
            and spec["nonce"] in action.get("text", "")
        ]
        passed = bool(matches)
        reason = ("nonce type action present" if passed
                  else f"no type action containing {spec['nonce']!r}")
    elif predicate == "finished":
        matches = [action for action in actions if action.get("action") == "finished"]
        passed = bool(matches)
        reason = "finished present" if passed else "completed screen was not finished"
    else:
        matches = [action for action in actions if action.get("action") != "finished"]
        premature = [action for action in actions if action.get("action") == "finished"]
        passed = bool(matches) and not premature
        reason = ("valid non-finished action present" if passed
                  else "incomplete screen produced no action or a premature finished")

    return {
        "passed": passed,
        "reason": reason,
        "predicate": predicate,
        "matching_actions": matches,
        "transformations": transforms,
    }


def _request_artifact(spec: dict, data_url: str, timeout_s: float) -> dict:
    """The exact one-turn request inputs used by call_holo_full."""
    return {
        "target": "local",
        "model": CFG.holo_model,
        "messages": holo.build_messages(spec["instruction"], data_url, history=[]),
        "response_format": holo.RESPONSE_SCHEMA,
        "max_tokens": 2048,
        "temperature": 0.8,
        "enable_thinking": True,
        "reasoning_effort": "medium",
        "max_history_images": CFG.holo_history_images,
        "timeout_s": timeout_s,
        "projection_basis": [WIDTH, HEIGHT],
    }


def _live_decide(case_dir: Path, data_url: str, instruction: str,
                 timeout_s: float) -> StepDecision:
    """One fresh production session, with its actual wire log kept beside the case."""
    previous_path = holo.REQUEST_LOG.path
    holo.REQUEST_LOG.path = str(case_dir / "wire.jsonl")
    try:
        call_fn = partial(holo.call_holo_full, timeout_s=timeout_s)
        session = holo.HoloSession(
            target="local",
            max_history_images=CFG.holo_history_images,
            call_fn=call_fn,
        )
        session.reset()
        return session.decide(data_url, WIDTH, HEIGHT, instruction)
    finally:
        holo.REQUEST_LOG.path = previous_path


def _serving_failures(snapshot: dict) -> list[str]:
    failures = []
    if not snapshot.get("reachable"):
        failures.append(
            f"endpoint {snapshot.get('endpoint')} unreachable: {snapshot.get('error')}")
    elif snapshot.get("configured") is False:
        failures.append(f"model {CFG.holo_model!r} is not configured")
    params = snapshot.get("params") or {}
    if snapshot.get("resident") and not params.get("has_mmproj", True):
        failures.append("resident vision model has no mmproj")
    return failures


def run_smoke(run_dir: Path, case_ids: list[str], timeout_s: float,
              snapshot: dict, decide_fn=None) -> int:
    """Run selected cases. Return 0=pass, 1=contract failure, 2=infrastructure."""
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.strftime("%Y%m%d_%H%M%S")
    meta = {
        "started": started,
        "cases": case_ids,
        "timeout_s": timeout_s,
        "screen_size": [WIDTH, HEIGHT],
        "model": CFG.holo_model,
        "endpoint": CFG.holo_local_url,
        "serving": snapshot,
    }
    _write_json(run_dir / "meta.json", meta)
    (run_dir / "system_prompt.txt").write_text(holo.SYSTEM_PROMPT)

    preflight = _serving_failures(snapshot)
    if preflight:
        summary = {
            "status": "infrastructure_error",
            "complete": False,
            "requested_cases": case_ids,
            "completed_cases": [],
            "failures": preflight,
        }
        _write_json(run_dir / "summary.json", summary)
        return 2

    results = []
    for case_id in case_ids:
        spec = CASES[case_id]
        case_dir = run_dir / case_id
        case_dir.mkdir()
        jpeg = _jpeg_bytes(render_case(case_id))
        frame_path = case_dir / "frame.jpg"
        frame_path.write_bytes(jpeg)
        data_url = holo.jpeg_bytes_to_data_url(jpeg)
        request = _request_artifact(spec, data_url, timeout_s)
        request["frame_sha256"] = hashlib.sha256(jpeg).hexdigest()
        _write_json(case_dir / "request.json", request)

        t0 = time.time()
        try:
            if decide_fn is None:
                decision = _live_decide(
                    case_dir, data_url, spec["instruction"], timeout_s)
            else:
                decision = decide_fn(
                    case_id=case_id,
                    data_url=data_url,
                    width=WIDTH,
                    height=HEIGHT,
                    instruction=spec["instruction"],
                    timeout_s=timeout_s,
                )
        except Exception as exc:  # endpoint/SDK/timeout boundary, preserved verbatim
            result = {
                "case": case_id,
                "status": "infrastructure_error",
                "passed": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "wall_time_s": round(time.time() - t0, 3),
            }
            _write_json(case_dir / "result.json", result)
            results.append(result)
            break

        _write_json(case_dir / "raw_response.json", decision.message)
        evaluation = evaluate_case(case_id, decision)
        result = {
            "case": case_id,
            "status": "pass" if evaluation["passed"] else "contract_failure",
            "passed": evaluation["passed"],
            "instruction": spec["instruction"],
            "expected": spec,
            "parsed_step": decision.step,
            "usage": decision.usage,
            "wall_time_s": round(time.time() - t0, 3),
            "evaluation": evaluation,
        }
        _write_json(case_dir / "result.json", result)
        results.append(result)

    infra = any(result["status"] == "infrastructure_error" for result in results)
    failed = any(not result["passed"] for result in results)
    complete = len(results) == len(case_ids)
    status = "infrastructure_error" if infra else (
        "contract_failure" if failed or not complete else "pass")
    summary = {
        "status": status,
        "complete": complete,
        "requested_cases": case_ids,
        "completed_cases": [result["case"] for result in results],
        "passed": sum(bool(result["passed"]) for result in results),
        "failed": sum(not bool(result["passed"]) for result in results),
        "results": [
            {"case": result["case"], "status": result["status"],
             "passed": result["passed"]}
            for result in results
        ],
    }
    _write_json(run_dir / "summary.json", summary)
    return 2 if infra else (1 if failed or not complete else 0)


def _new_run_dir() -> Path:
    while True:
        path = Path(CFG.runs_dir) / (
            f"model_contract_smoke_{time.strftime('%Y%m%d_%H%M%S')}")
        try:
            path.mkdir(parents=True, exist_ok=False)
            return path
        except FileExistsError:
            time.sleep(1.0)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="controlled live-model contract smoke (no physical rig)")
    parser.add_argument("--case", choices=CASE_ORDER, action="append", dest="cases",
                        help="run only this case; repeat to select multiple")
    parser.add_argument("--timeout-s", type=float, default=45.0,
                        help="per-model-call HTTP timeout (default: 45)")
    args = parser.parse_args()
    if args.timeout_s <= 0:
        parser.error("--timeout-s must be positive")

    case_ids = list(dict.fromkeys(args.cases or CASE_ORDER))
    run_dir = _new_run_dir()

    def emit(message: str) -> None:
        print(message)
        with (run_dir / "console.txt").open("a") as stream:
            stream.write(message + "\n")

    emit(f"[model-contract] {len(case_ids)} case(s), artifacts -> {run_dir}")
    try:
        snapshot = serving_snapshot(model=CFG.holo_model)
    except Exception as exc:
        snapshot = {
            "endpoint": CFG.holo_local_url,
            "reachable": False,
            "configured": None,
            "resident": None,
            "params": {},
            "error": f"{type(exc).__name__}: {exc}",
        }
    code = run_smoke(run_dir, case_ids, args.timeout_s, snapshot)
    summary = json.loads((run_dir / "summary.json").read_text())
    for result in summary.get("results", []):
        emit(f"[model-contract] {result['case']}: {result['status']}")
    for failure in summary.get("failures", []):
        emit(f"[model-contract] infrastructure: {failure}")
    emit(f"[model-contract] {summary['status']} -> {run_dir}/summary.json")
    return code


if __name__ == "__main__":
    sys.exit(main())
