#!/usr/bin/env python3
"""probe_resolution_ab.py — 720p vs 1080p model-input A/B at history=3 (2026-07-21).

Measures what the resolution knob actually costs/saves on THIS rig: per-step prompt
tokens, completion tokens, and wall time, against the live local holo3.1 endpoint, using
a realistic mid-run request (system prompt + 3 prior steps of observation/assistant/
tool_output history + current observation, trimmed to the last 3 images -- exactly what
agent_loop_holo.run() sends at depth 3 on the native-verbatim line).

NOT a quality benchmark: grounding accuracy at 720p vs 1080p is a separate question
(2026-07-19 showed real coordinate misses on dense UIs at 720p -- docs/native line
defaults to 1080 for that reason). This probe answers only "how much processing time/
tokens does 720p save", so the quality-vs-cost tradeoff can be made with real numbers.

Usage:
    cd <repo root>
    PYTHONPATH=. python3 tools/probe_resolution_ab.py [--reps 5] [--image path.png]
    (no --image: boots the rig and captures a fresh frame; --image skips hardware)
"""
import argparse
import base64
import io
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kvm_agent.hardware.env import model_input_jpeg
from kvm_agent.models.holo import (
    call_holo_full, observation_message, tool_output_message, SYSTEM_PROMPT,
)

# A realistic prior-step assistant JSON (shape matches RESPONSE_SCHEMA; content length
# is typical of native-style note+thought).
_PRIOR_STEP_JSON = json.dumps({
    "note": "Notepad is open and focused; document contains 'hello world'.",
    "thought": "The previous write succeeded (text visible). Continue with the next sub-goal.",
    "tool_calls": [{"tool_name": "write_desktop", "content": "hello world", "press_enter": False}],
})


def make_jpeg(png_bytes: bytes, target_h: int) -> bytes:
    """Decode + the SHARED resize/encode core (kvm_agent.hardware.env.model_input_jpeg)
    -- no local re-implementation to drift from Camera.model_input_jpeg (2026-07-21 review)."""
    import cv2
    import numpy as np
    arr = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    return model_input_jpeg(arr, target_h)


def data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


def build_history(img_url: str) -> list[dict]:
    """3 prior steps of observation + assistant + tool_output history (images included --
    trim_to_last_n_images(n=3) inside call_holo_full keeps the newest 3 total)."""
    history = []
    for _ in range(3):
        history.append(observation_message(img_url))
        history.append({"role": "assistant", "content": _PRIOR_STEP_JSON})
        history.append(tool_output_message("write_desktop", "Executed. Screen changed (max tile diff 5.2, region center)."))
    return history


def run_probe(png_bytes: bytes, screen_w: int, screen_h: int, reps: int, resolutions=(720, 1080)):
    results = {}
    warmed = False
    for res in resolutions:
        img_url = data_url(make_jpeg(png_bytes, res))
        rows = []
        for k in range(reps):
            t0 = time.time()
            step, message, usage = call_holo_full(
                "Describe the current screen state.", img_url, screen_w, screen_h,
                history=build_history(img_url), max_history_images=3)
            dt = time.time() - t0
            row = {
                "wall_s": round(dt, 2),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "actions": len(step.get("actions", [])),
                "error": step.get("error"),
            }
            tag = " (warmup/model load -- excluded)" if not warmed else ""
            print(f"  [{res}p rep {k + 1}]{tag} {row}")
            if not warmed:
                warmed = True   # first call pays the llama-swap model load; not a measurement
                continue
            rows.append(row)
        results[res] = rows
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=5, help="calls per resolution (first overall call is warmup)")
    ap.add_argument("--image", help="use this PNG instead of capturing from the rig")
    args = ap.parse_args()

    if args.image:
        png_bytes = Path(args.image).read_bytes()
        from PIL import Image
        with Image.open(io.BytesIO(png_bytes)) as im:
            screen_w, screen_h = im.size
        print(f"[probe] source image {args.image} ({screen_w}x{screen_h})")
    else:
        import agent_loop_holo
        agent_loop_holo.boot()
        frame = agent_loop_holo.ENV.cam.read()
        import cv2
        ok, buf = cv2.imencode(".png", frame)
        png_bytes = buf.tobytes()
        screen_w, screen_h = agent_loop_holo.ENV.screen_width, agent_loop_holo.ENV.screen_height
        print(f"[probe] captured live frame from rig ({screen_w}x{screen_h})")
        import atexit
        atexit.register(agent_loop_holo.shutdown)  # clean camera-thread teardown (flaw #5)

    print(f"[probe] system prompt: {len(SYSTEM_PROMPT)} chars; reps={args.reps} per resolution; history depth=3")
    results = run_probe(png_bytes, screen_w, screen_h, args.reps)

    print("\n=== A/B results (means over measured reps; warmup excluded) ===")
    print(f"{'res':>6} {'prompt_tok':>11} {'compl_tok':>10} {'wall_s':>8} {'reps':>5}")
    summary = {}
    for res, rows in results.items():
        if not rows:
            print(f"{res:>6}  (no measured reps -- increase --reps)")
            continue
        mp = statistics.mean(r["prompt_tokens"] for r in rows)
        mc = statistics.mean(r["completion_tokens"] for r in rows)
        mw = statistics.mean(r["wall_s"] for r in rows)
        errs = sum(1 for r in rows if r["error"])
        summary[res] = (mp, mc, mw)
        print(f"{res:>6} {mp:>11.0f} {mc:>10.0f} {mw:>8.1f} {len(rows):>5}  errors={errs}")
    verdict = None
    if 720 in summary and 1080 in summary:
        p720, p1080 = summary[720], summary[1080]
        verdict = (f"720p vs 1080p: prompt tokens {p720[0] / p1080[0]:.1%} of 1080p "
                   f"(saves {p1080[0] - p720[0]:.0f}/step), wall {p720[2] / p1080[2]:.1%} "
                   f"of 1080p (saves {p1080[2] - p720[2]:.1f}s/step)")
        print(f"\n{verdict}")
        print("Note: wall time includes completion (reasoning trace) variance, which can "
              "swamp the prompt-side saving at these step sizes -- see the spread above.")

    # The primary result must land in runs/, not stdout only (AGENTS.md §1;
    # 2026-07-21 review: this was the one tool whose output never did).
    from kvm_agent.config import CFG
    out_dir = Path(CFG.runs_dir) / f"probe_resolution_ab_{time.strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "screen": [screen_w, screen_h], "reps": args.reps,
        "source": args.image or "live rig capture",
        "per_resolution": {str(res): rows for res, rows in results.items()},
        "means": {str(res): {"prompt_tokens": m[0], "completion_tokens": m[1],
                             "wall_s": m[2]} for res, m in summary.items()},
        "verdict": verdict,
    }
    out_path = out_dir / "results.json"
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\n[probe] results -> {out_path}")


if __name__ == "__main__":
    main()
