#!/usr/bin/env python3
"""One bounded physical capture→model→HID calibration through the production loop.

The target is a repository-owned static page. It never reports its state to this
driver; captured pixels are the only completion oracle.

    python tools/physical_calibration_smoke.py
    python tools/physical_calibration_smoke.py --host-ip 192.168.0.10 --seed 7319
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from functools import partial
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import traceback
from urllib.parse import urlsplit

import cv2
import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
PAGE_TEMPLATE = Path(__file__).with_name("physical_calibration_target.html")
RUNS_ROOT = REPO_ROOT / "runs"
ACTOR_TAG = "physical_calibration_actor"
PORT = 8765
MAX_STEPS = 6
MODEL_TIMEOUT_S = 60.0
SETUP_TIMEOUT_S = 30.0

MARKER_MIN = 0.004
STAGE_MIN = 0.12
ERROR_MIN = 0.003
TARGET_MIN = 0.002
POSITIONS = ((12, 38), (58, 38), (12, 62), (58, 62))


class CalibrationFailure(RuntimeError):
    def __init__(self, boundary: str, message: str):
        super().__init__(message)
        self.boundary = boundary


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
            stream.flush()
        return len(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def calibration_spec(seed: int) -> dict:
    """Stable seed→location/nonce mapping, recorded beside every run."""
    digest = hashlib.sha256(f"kvm-physical-calibration:{seed}".encode()).digest()
    left, top = POSITIONS[digest[0] % len(POSITIONS)]
    nonce = f"KVM-{int.from_bytes(digest[1:3], 'big') % 10000:04d}"
    return {"seed": int(seed), "nonce": nonce, "button_left_pct": left,
            "button_top_pct": top}


def render_page(spec: dict) -> str:
    page = PAGE_TEMPLATE.read_text()
    replacements = {
        "__SEED__": str(spec["seed"]),
        "__NONCE__": spec["nonce"],
        "__BUTTON_LEFT__": str(spec["button_left_pct"]),
        "__BUTTON_TOP__": str(spec["button_top_pct"]),
    }
    for marker, value in replacements.items():
        page = page.replace(marker, value)
    unresolved = [marker for marker in replacements if marker in page]
    if unresolved:
        raise ValueError(f"unresolved calibration-page marker(s): {unresolved}")
    return page


def _bounds(mask: np.ndarray, minimum: float) -> list[int] | None:
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8)
    if count <= 1:
        return None
    component = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[component, cv2.CC_STAT_AREA] / mask.size < minimum:
        return None
    x, y, width, height = (int(value) for value in stats[component, :4])
    return [x, y, x + width - 1, y + height - 1]


def visual_oracle(frame: np.ndarray | None) -> dict:
    """Classify the static page from broad, analog-capture-tolerant color regions."""
    if frame is None or frame.ndim != 3 or frame.shape[2] < 3:
        return {
            "page_visible": False, "stage": "absent", "success": False,
            "input_rejected": False, "marker_fraction": 0.0,
            "entry_fraction": 0.0, "success_fraction": 0.0,
            "error_fraction": 0.0, "action_target_bounds": None,
        }

    b, g, r = (frame[:, :, index].astype(np.int16) for index in range(3))
    marker = (r > 170) & (b > 150) & (g < 125) & ((r - g) > 70)
    entry = (r > 155) & (g > 115) & (b < 145) & ((r - b) > 55)
    success = (g > 125) & (r < 105) & (b < 175) & ((g - r) > 55)
    error = (r > 145) & (g < 100) & (b < 100) & ((r - g) > 75)
    target = (b > 165) & (g > 45) & (g < 165) & (r < 90) & ((b - r) > 100)

    marker_fraction = float(marker.mean())
    entry_fraction = float(entry.mean())
    success_fraction = float(success.mean())
    error_fraction = float(error.mean())
    page_visible = marker_fraction >= MARKER_MIN
    is_success = page_visible and success_fraction >= STAGE_MIN
    if is_success:
        stage = "success"
    elif page_visible and entry_fraction >= STAGE_MIN:
        stage = "entry"
    elif page_visible:
        stage = "start"
    else:
        stage = "absent"
    return {
        "page_visible": page_visible,
        "stage": stage,
        "success": is_success,
        "input_rejected": page_visible and error_fraction >= ERROR_MIN,
        "marker_fraction": marker_fraction,
        "entry_fraction": entry_fraction,
        "success_fraction": success_fraction,
        "error_fraction": error_fraction,
        "action_target_bounds": (
            _bounds(target, TARGET_MIN) if stage in ("start", "entry") else None),
    }


def _inside(bounds: list[int] | None, coordinate) -> bool | None:
    if bounds is None or not isinstance(coordinate, (list, tuple)) \
            or len(coordinate) != 2:
        return None
    x1, y1, x2, y2 = bounds
    x, y = coordinate
    return x1 <= x <= x2 and y1 <= y <= y2


def _png_bytes(frame: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", frame)
    if not ok:
        raise RuntimeError("OpenCV failed to encode calibration evidence frame")
    return encoded.tobytes()


def inspect_actor_run(actor_dir: Path | None) -> dict:
    """Read production RunRecorder output and tie every claim to its decision frame."""
    if actor_dir is None or not actor_dir.is_dir():
        return {"complete_evidence": False, "problem": "actor run directory missing",
                "steps": [], "finished_claim": None}

    steps = []
    problems = []
    finished_claim = None
    for record_path in sorted(actor_dir.glob("step_*.json")):
        try:
            record = json.loads(record_path.read_text())
        except Exception as exc:
            problems.append(f"{record_path.name}: {type(exc).__name__}: {exc}")
            continue
        frame_path = record_path.with_suffix(".png")
        frame = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
        if frame is None:
            problems.append(f"{frame_path.name}: missing or unreadable")
        oracle = visual_oracle(frame)
        parsed = record.get("action") if isinstance(record.get("action"), dict) else {}
        actions = parsed.get("actions") or []
        clicks = []
        for action in actions:
            if action.get("action") in ("left_click", "double_click"):
                clicks.append({
                    "coordinate": action.get("coordinate"),
                    "inside_visible_action_target": _inside(
                        oracle["action_target_bounds"], action.get("coordinate")),
                })
        step = {
            "step": record.get("step"),
            "record": record_path.name,
            "frame": frame_path.name,
            "executed": record.get("executed"),
            "stage": oracle["stage"],
            "oracle": oracle,
            "actions": actions,
            "clicks": clicks,
            "error": parsed.get("error"),
        }
        steps.append(step)
        if finished_claim is None and any(
                action.get("action") == "finished" for action in actions):
            finished_claim = {
                "step": step["step"],
                "record": step["record"],
                "frame": step["frame"],
                "decision_stage": oracle["stage"],
                "decision_frame_success": oracle["success"],
            }
    return {
        "complete_evidence": not problems and bool(steps),
        "problem": "; ".join(problems) if problems else None,
        "steps": steps,
        "finished_claim": finished_claim,
    }


def _outcome(boundary: str | None, reason: str) -> dict:
    return {
        "status": "pass" if boundary is None else "calibration_failure",
        "first_broken_boundary": boundary,
        "reason": reason,
    }


def classify_result(run_result: dict, final: dict, evidence: dict, nonce: str) -> dict:
    """Name the first boundary supported by the recorded frames/actions."""
    if not evidence.get("complete_evidence"):
        return _outcome(
            "capture", evidence.get("problem") or "actor evidence is incomplete")

    errors = [step["error"] for step in evidence["steps"] if step.get("error")]
    if any("model call failed" in str(error) for error in errors):
        return _outcome("serving", "production session recorded a model-call failure")
    if errors:
        return _outcome(
            "request_parse", f"production parser rejected a response: {errors[0]}")

    claim = evidence.get("finished_claim")
    if claim and not claim["decision_frame_success"]:
        return _outcome(
            "termination_protocol",
            f"finished was decided from {claim['decision_stage']!r}, not success")
    if final.get("success"):
        if run_result.get("finished") and claim:
            return _outcome(None, "page success and finished were both observed in order")
        return _outcome(
            "termination_protocol", "page reached success but the loop did not finish")
    if not final.get("page_visible"):
        return _outcome("page_oracle", "calibration page disappeared before final capture")

    stages = [step["stage"] for step in evidence["steps"]]
    if "entry" not in stages and "success" not in stages:
        clicks = [click for step in evidence["steps"] if step["stage"] == "start"
                  for click in step["clicks"]]
        if any(click["inside_visible_action_target"] is True for click in clicks):
            return _outcome("hid", "in-bounds START click did not advance the page")
        if clicks:
            return _outcome("coordinate", "START click missed the visible target")
        return _outcome("action_selection", "no START click was produced")

    entry = [step for step in evidence["steps"] if step["stage"] == "entry"]
    typed = [action for step in entry for action in step["actions"]
             if action.get("action") == "type"]
    if not typed or not any(nonce in str(action.get("text", "")) for action in typed):
        return _outcome("action_selection", "displayed nonce was not typed")
    submitted = any(action.get("press_enter") for action in typed) or any(
        click["inside_visible_action_target"] is True
        for step in entry for click in step["clicks"])
    if not submitted:
        return _outcome("coordinate", "no in-bounds SUBMIT action was recorded")
    if final.get("input_rejected"):
        return _outcome("focus", "correct type action was recorded but value was rejected")
    return _outcome("hid", "correct entry actions did not produce page success")


def discover_host_ip(appliance_url: str) -> str:
    """Pick the source address used to reach the appliance's target-side LAN."""
    parsed = urlsplit(appliance_url)
    if not parsed.hostname:
        raise ValueError(f"cannot discover route from appliance URL {appliance_url!r}")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((parsed.hostname, parsed.port or 80))
        return str(sock.getsockname()[0])


def _start_server(page: str, access_log: Path):
    payload = page.encode()
    log_lock = threading.Lock()

    class Server(ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    class Handler(BaseHTTPRequestHandler):
        def _record(self, status):
            record = {
                "ts": time.time(), "client": self.client_address[0],
                "method": self.command, "path": self.path, "status": status,
            }
            with log_lock, access_log.open("a") as stream:
                stream.write(json.dumps(record) + "\n")

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)
                self._record(200)
            elif path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                self._record(204)
            else:
                self.send_error(404)
                self._record(404)

        def log_message(self, _format, *_args):
            pass

    server = Server(("0.0.0.0", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _wait_for_stage(cam, stage: str, timeout_s: float):
    deadline = time.monotonic() + timeout_s
    last_frame = None
    last_oracle = visual_oracle(None)
    while time.monotonic() < deadline:
        last_frame = cam.read()
        last_oracle = visual_oracle(last_frame)
        if last_oracle["stage"] == stage:
            return last_frame, last_oracle
        time.sleep(0.25)
    return last_frame, last_oracle


def _new_run_dir() -> Path:
    while True:
        path = RUNS_ROOT / f"physical_calibration_smoke_{time.strftime('%Y%m%d_%H%M%S')}"
        try:
            path.mkdir(parents=True, exist_ok=False)
            return path
        except FileExistsError:
            time.sleep(1.0)


def _actor_dir(run_dir: Path) -> Path | None:
    candidates = sorted(
        path for path in run_dir.glob(f"{ACTOR_TAG}_*") if path.is_dir())
    return candidates[-1] if candidates else None


def _boot_boundary(exc: BaseException) -> str:
    message = str(exc).lower()
    if "frame" in message or "capture" in message or "camera" in message:
        return "capture"
    if "hid" in message or "appliance" in message or "/hid/" in message:
        return "hid"
    return "infrastructure"


def run_physical(seed: int, host_ip: str | None, run_dir: Path) -> int:
    """Execute one no-retry calibration. Return 0=pass, 1=behavior, 2=infrastructure."""
    os.environ["RUNS_DIR"] = str(run_dir)
    os.environ["LOGS_DIR"] = str(run_dir / "logs")
    sys.path.insert(0, str(REPO_ROOT))

    spec = calibration_spec(seed)
    page = render_page(spec)
    (run_dir / "page.html").write_text(page)
    _write_json(run_dir / "spec.json", spec)

    server = None
    loop = None
    page_open = False
    cleanup_warnings = []
    phase = "import"
    result = None
    access_log = run_dir / "http_requests.jsonl"

    try:
        import agent_loop_holo as loop
        from kvm_agent.config import CFG
        from kvm_agent.models.holo import HoloSession, call_holo_full

        if CFG.target_shell != "gnome":
            raise CalibrationFailure(
                "setup", f"visible Alt+F2 setup requires TARGET_SHELL=gnome, got "
                         f"{CFG.target_shell!r}")

        host_ip = host_ip or discover_host_ip(CFG.appliance_url)
        phase = "page_serving"
        server = _start_server(page, access_log)
        target_url = f"http://{host_ip}:{PORT}/?seed={spec['seed']}"
        meta = {
            "seed": spec["seed"],
            "target_url": target_url,
            "port": PORT,
            "target_shell": CFG.target_shell,
            "screen_size": list(CFG.screen_size),
            "model": CFG.holo_model,
            "endpoint": CFG.holo_local_url,
            "max_steps": MAX_STEPS,
            "per_model_call_timeout_s": MODEL_TIMEOUT_S,
            "setup_timeout_s": SETUP_TIMEOUT_S,
            "truth_channel": "captured pixels only",
            "automatic_retries": 0,
        }
        _write_json(run_dir / "meta.json", meta)
        print(f"[calibration] page serving at {target_url}")

        phase = "boot"
        loop.boot(verify=True, serving_check=True)
        serving = dict(loop.SERVING)
        if not serving.get("reachable"):
            raise CalibrationFailure(
                "serving", f"model endpoint unreachable: {serving.get('error')}")
        if serving.get("configured") is False:
            raise CalibrationFailure(
                "serving", f"model {CFG.holo_model!r} is not configured")
        if serving.get("resident") and not (
                serving.get("params") or {}).get("has_mmproj", True):
            raise CalibrationFailure("serving", "resident model has no mmproj")

        phase = "setup"
        loop.ENV.r4.combo("alt+f2")
        time.sleep(0.75)
        loop.ENV.r4.type(f"firefox --new-window {target_url}")
        loop.ENV.r4.key("enter")
        setup_frame, setup_oracle = _wait_for_stage(
            loop.ENV.cam, "start", SETUP_TIMEOUT_S)
        if setup_frame is not None:
            (run_dir / "setup_frame.png").write_bytes(_png_bytes(setup_frame))
        _write_json(run_dir / "setup_oracle.json", setup_oracle)
        if setup_oracle["stage"] != "start":
            requests_seen = sum(1 for _ in access_log.open()) if access_log.exists() else 0
            boundary = "page_oracle" if requests_seen else "page_serving"
            raise CalibrationFailure(
                boundary, f"calibration start page not visible after "
                          f"{SETUP_TIMEOUT_S:.1f}s (HTTP requests={requests_seen}, "
                          f"oracle stage={setup_oracle['stage']})")
        page_open = True

        phase = "actor"
        task = (
            "Complete the Model Integration Calibration visible in the browser. "
            "Click START CALIBRATION. On the next screen, click the verification-code "
            "field, type the code shown on that screen exactly, and submit it. "
            "Call finished only after a later screenshot visibly shows "
            "CALIBRATION SUCCESS."
        )
        session = HoloSession(
            target="local",
            max_history_images=CFG.holo_history_images,
            call_fn=partial(call_holo_full, timeout_s=MODEL_TIMEOUT_S),
        )
        run_result = loop.run(
            task,
            max_steps=MAX_STEPS,
            target="local",
            confirm_first=0,
            record=True,
            tag=ACTOR_TAG,
            no_progress_abort=True,
            session=session,
            verify_mode="off",
        )

        phase = "oracle"
        time.sleep(0.5)
        final_frame = loop.ENV.cam.read()
        final_oracle = visual_oracle(final_frame)
        if final_frame is not None:
            (run_dir / "final_frame.png").write_bytes(_png_bytes(final_frame))
        _write_json(run_dir / "final_oracle.json", final_oracle)
        actor_dir = _actor_dir(run_dir)
        evidence = inspect_actor_run(actor_dir)
        _write_json(run_dir / "actor_evidence.json", evidence)
        classification = classify_result(
            run_result, final_oracle, evidence, spec["nonce"])
        result = {
            **classification,
            "run_result": run_result,
            "final_oracle": final_oracle,
            "actor_run": (
                str(actor_dir.relative_to(run_dir)) if actor_dir is not None else None),
            "seed": spec["seed"],
            "max_steps": MAX_STEPS,
            "per_model_call_timeout_s": MODEL_TIMEOUT_S,
        }
    except CalibrationFailure as exc:
        result = {
            "status": "infrastructure_error",
            "first_broken_boundary": exc.boundary,
            "reason": str(exc),
            "phase": phase,
            "seed": spec["seed"],
        }
    except BaseException as exc:
        traceback.print_exc()
        boundary = _boot_boundary(exc) if phase == "boot" else (
            "capture" if phase == "oracle" else "infrastructure")
        result = {
            "status": "infrastructure_error",
            "first_broken_boundary": boundary,
            "reason": f"{type(exc).__name__}: {exc}",
            "phase": phase,
            "seed": spec["seed"],
        }
    finally:
        if loop is not None and getattr(loop, "ENV", None) is not None:
            try:
                cleanup_oracle = visual_oracle(loop.ENV.cam.read())
                if page_open and cleanup_oracle["page_visible"]:
                    loop.ENV.r4.combo("alt+f4")
                    time.sleep(0.75)
            except Exception as exc:
                cleanup_warnings.append(
                    f"browser cleanup: {type(exc).__name__}: {exc}")
            try:
                loop.shutdown()
            except Exception as exc:
                cleanup_warnings.append(
                    f"hardware shutdown: {type(exc).__name__}: {exc}")
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception as exc:
                cleanup_warnings.append(
                    f"page server shutdown: {type(exc).__name__}: {exc}")

    result = result or {
        "status": "infrastructure_error",
        "first_broken_boundary": "infrastructure",
        "reason": "calibration ended without a result",
    }
    if cleanup_warnings:
        result["cleanup_warnings"] = cleanup_warnings
    _write_json(run_dir / "summary.json", result)
    print(f"[calibration] {result['status']}: {result['reason']}")
    print(f"[calibration] evidence -> {run_dir}")
    if result["status"] == "pass":
        return 0
    return 2 if result["status"] == "infrastructure_error" else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="bounded physical model/harness calibration (no retries)")
    parser.add_argument("--seed", type=int, default=None,
                        help="reproducible page seed (default: generated and recorded)")
    parser.add_argument("--host-ip",
                        help="host address reachable from the target laptop")
    args = parser.parse_args()
    if args.seed is None:
        args.seed = int(time.time_ns() & 0x7fffffff)

    run_dir = _new_run_dir()
    with (run_dir / "console.txt").open("a", buffering=1) as console:
        with redirect_stdout(_Tee(sys.stdout, console)), \
                redirect_stderr(_Tee(sys.stderr, console)):
            print(f"[calibration] seed={args.seed}, artifacts -> {run_dir}")
            return run_physical(args.seed, args.host_ip, run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
