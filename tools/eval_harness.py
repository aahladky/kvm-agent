"""
eval_harness.py — repeatable evaluation harness for the EvoCUA calculator probe.

Mirrors the repo's structure (lib_run_single rollout + run_multienv repeat/aggregate)
but against the PHYSICAL KVM rig (HDMI capture obs + Pico HID action) instead of an
OSWorld VM. It supplies the two things the repo gets from OSWorld that we lack:

  - env.reset() analog : AC-click reset between reps (RESET_COORD) so each rep starts
                         from display 0. (Weaker than a VM snapshot — no true restore.)
  - env.evaluate() analog : saves the END-STATE frame for every run so the display can
                         be verified visually / OCR'd by score_batch.py (run in the
                         Linux sandbox where tesseract is available). Plus a coord-only
                         grounding metric (operator-click column) computed from the logs
                         with NO OCR dependency on the desktop.

INFERENCE CONFIG is held at REPO-SPEC and never varied between runs:
  temperature 0.01 (set in evocua.ground), top_p 0.9, coordinate_type relative,
  resize_factor 32, settle 5.0 (= sleep_after_execution), max_steps 50.
Only the hardware-forced deviations remain (GGUF via Ollama, num_ctx 16384 cap).
The ONLY things this harness sweeps are MODEL + HISTORY depth (CONFIGS) — one
variable at a time, everything else locked.

Run on the Windows desktop (holds the capture card + drives the Pico). Pre-open the
Calculator. Then score with score_batch.py in the sandbox.
"""
import os
import re
import json
import time
import cv2
import evocua
from openai import OpenAI
from r4_client import R4
from agent_loop_evocua import Camera, do_action, CAM_INDEX, MODEL_URL

# ---- repo-faithful, locked (do not vary between runs) ----
SETTLE_SEC = 5.0      # = repo sleep_after_execution
MAX_STEPS  = 50       # = repo max_steps (model budget; kept at the repo value)
ABORT_STEPS = 18      # harness-level early-stop: this microtask is ~7 steps clean /
                      # ~14 with one recovery; beyond 18 it's hopeless flailing. Bounds
                      # wasted time only — does NOT affect the grounding measurement.
                      # No repo analog (OSWorld tasks legitimately use all 50 steps).
# evocua.ground already uses temperature=0.01, top_p=0.9; coords relative; resize 32.
# ----------------------------------------------------------

# env.reset() analog: AC button location in 1920x1080 capture pixels (from logs).
RESET_COORD = (534, 630)
RESET_SETTLE = 1.5

# Operator-column classifier: digit columns sit at x≈534/578/630; the orange
# operator column at x≈672. Midpoint ~651 separates digit-col from operator-col.
OP_COL_X = 651

# Map the model's Action-line wording -> operator symbol (for the grounding metric).
OPERATOR_WORDS = [
    ("addition", "+"), ("plus", "+"), ("add", "+"),
    ("subtraction", "-"), ("minus", "-"), ("subtract", "-"),
    ("multiplication", "*"), ("multipl", "*"), ("times", "*"),
    ("division", "/"), ("divi", "/"),
    ("equals", "="), ("equal", "="),
]

# Task suite: goal + expected final display. Distinct digits so re-click detection
# isn't fooled by a legitimately repeated button. Probes operators at different rows.
TASKS = [
    {"name": "7x8+5", "goal": "Using the open Calculator, compute 7 × 8 + 5", "expected": "61"},
]
# Configs to sweep — ONE variable at a time, everything else locked above.
# Add rows to compare (e.g. Q8 vs clean-Q5, or history 1 vs 4). Pre-load each model
# in Ollama first (or the first call pays the cold-load).
CONFIGS = [
    {"model": "evocua-8b-q5-clean", "history": 4},
    {"model": "evocua-8b-q5-clean", "history": 1},
]

REPS = 12
BATCH_DIR = os.path.join("runs", "batch_" + time.strftime("%Y%m%d_%H%M%S"))


def classify_operator(action_line):
    """Return the operator symbol if the model's Action line names one, else None."""
    s = (action_line or "").lower()
    for word, sym in OPERATOR_WORDS:
        if word in s:
            return sym
    return None


def settle(cam, secs):
    end = time.time() + secs
    while time.time() < end:
        f = cam.read()
        if f is not None:
            cv2.imshow("capture", f)
        cv2.waitKey(15)


def reset_calc(r4, cam):
    """env.reset() analog — AC-click to clear the display to 0 before each rep."""
    x, y = RESET_COORD
    r4.move(x, y); r4.click()
    time.sleep(RESET_SETTLE)


def run_one(client, cam, r4, model, history_n, goal, outdir):
    os.makedirs(outdir, exist_ok=True)
    evocua.HISTORY_TURNS = history_n          # harmless; ground() also gets it explicitly
    screenshots, responses, steps = [], [], []
    finished, term_status = False, None
    recent = []                          # last clicks, for the re-click-loop guard

    for it in range(MAX_STEPS):
        frame = cam.read()
        fh, fw = frame.shape[:2]
        b64, pw, ph = evocua.process_image(frame)
        screenshots.append(b64)

        t = time.time()
        raw = evocua.ground(client, model, screenshots, responses, pw, ph, goal, history_n)
        responses.append(raw)
        lat = round(time.time() - t, 2)

        inp = evocua.parse_action(raw, pw, ph, fw, fh)
        action_line = evocua.extract_action_line(raw)

        if not inp:
            steps.append({"i": it, "latency": lat, "action_line": action_line, "parsed": None})
            continue
        if inp["action"] == "finished":
            finished, term_status = True, inp.get("text")
            steps.append({"i": it, "latency": lat, "action_line": action_line,
                          "action": "finished", "status": term_status})
            break

        desc = do_action(r4, inp)
        coord = inp.get("coordinate")
        op = classify_operator(action_line)
        rec = {"i": it, "latency": lat, "action_line": action_line,
               "action": inp["action"], "coord": coord, "operator": op}
        if op and coord:
            rec["op_x"] = coord[0]
            rec["op_in_operator_col"] = bool(coord[0] >= OP_COL_X)
        steps.append(rec)
        print(f"  [{it}] {lat:>5}s  {desc}" + (f"   <op {op} x={coord[0]} "
              f"{'OPcol' if coord[0] >= OP_COL_X else 'DIGITcol'}>" if op and coord else ""))

        # re-click-loop early-abort: if the last 6 clicks collapse to <=2 distinct
        # spots, the model is stuck (covers single-coord repeats AND 2-coord
        # oscillation, e.g. rep01's 576,812 <-> 576,732). A legit run visits ~6
        # distinct buttons, so this won't false-trigger on distinct-digit tasks.
        if coord:
            recent = (recent + [coord])[-6:]
            if len(recent) >= 6:
                clusters = []
                for c in recent:
                    if not any(abs(c[0] - k[0]) < 15 and abs(c[1] - k[1]) < 15
                               for k in clusters):
                        clusters.append(c)
                if len(clusters) <= 2:
                    print("  re-click loop -> aborting run (stuck)")
                    steps.append({"i": it, "aborted": "reclick_loop"})
                    break

        if it + 1 >= ABORT_STEPS:
            print("  step budget exceeded -> aborting run (flail)")
            steps.append({"i": it, "aborted": "step_budget"})
            break
        settle(cam, SETTLE_SEC)
    else:
        print("  hit MAX_STEPS cap")

    # env.evaluate() artifact: save the end-state for visual / OCR verification.
    final = cam.read()
    cv2.imwrite(os.path.join(outdir, "end_full.png"), final)

    record = {"model": model, "history": history_n, "goal": goal,
              "finished": finished, "term_status": term_status,
              "iters": len(steps), "steps": steps}
    with open(os.path.join(outdir, "run.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    return record


def main():
    os.makedirs(BATCH_DIR, exist_ok=True)
    client = OpenAI(base_url=MODEL_URL, api_key="ollama")
    r4 = R4(); print("R4 connected")

    cam = Camera(CAM_INDEX)
    t0 = time.time()
    while cam.read() is None:
        if time.time() - t0 > 5:
            raise SystemExit("no frames — capture card free?")
        time.sleep(0.05)
    f0 = cam.read()
    print(f"capture dims: {f0.shape[1]}x{f0.shape[0]}  (must equal Pico SCREEN_W/H)")
    print(f"batch -> {BATCH_DIR}\n")

    manifest = []
    try:
        for cfg in CONFIGS:
            for task in TASKS:
                for rep in range(REPS):
                    name = f"{cfg['model']}__h{cfg['history']}__{task['name']}__rep{rep:02d}"
                    print(f"=== {name} ===")
                    reset_calc(r4, cam)
                    outdir = os.path.join(BATCH_DIR, name)
                    rec = run_one(client, cam, r4, cfg["model"], cfg["history"],
                                  task["goal"], outdir)
                    ops = [s for s in rec["steps"] if s.get("operator")]
                    entry = {
                        "dir": name, "model": cfg["model"], "history": cfg["history"],
                        "task": task["name"], "expected": task["expected"], "rep": rep,
                        "finished": rec["finished"], "term_status": rec["term_status"],
                        "iters": rec["iters"],
                        "operator_clicks": [
                            {"op": s["operator"], "x": (s.get("coord") or [None])[0],
                             "in_op_col": s.get("op_in_operator_col")} for s in ops],
                    }
                    manifest.append(entry)
                    with open(os.path.join(BATCH_DIR, "manifest.json"), "w",
                              encoding="utf-8") as f:
                        json.dump(manifest, f, indent=2, ensure_ascii=False)
                    print(f"  -> finished={rec['finished']} iters={rec['iters']}\n")
    finally:
        cam.release(); r4.close(); cv2.destroyAllWindows()
        print(f"\nBatch complete -> {BATCH_DIR}")
        print("Next: run score_batch.py in the sandbox to OCR end_full.png + aggregate.")


if __name__ == "__main__":
    main()
