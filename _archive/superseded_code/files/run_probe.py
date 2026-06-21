"""
run_probe.py — repeatable probe using the OFFICIAL EvoCUAAgent (S1 or S2) against
the physical rig via pico_env. Replaces the from-scratch agent_loop_evocua.py +
evocua.py: the model/inference code is now the authors' (correct for both modes,
coordinate types, history, retry); we own only the deterministic hardware shim.

Per-run it logs the operator-click column (grounding metric, no OCR) and saves the
end-state frame; success is OCR'd afterward by score_batch.py in the sandbox
(manifest schema is score_batch-compatible).

Inference config is the repo's, swept ONE axis at a time via CONFIGS. The hardware
deviations (GGUF/Ollama, num_ctx 16384) are unchanged.

Run on the Windows desktop. Pre-open the Calculator. Kill any other process holding
the capture card first (e.g. an eval_harness batch).
"""
import os
import re
import sys
import json
import time
import cv2

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evocua")
sys.path.insert(0, REPO)

# Point the official agent's OpenAI client at our Ollama server.
os.environ["OPENAI_BASE_URL"] = "http://192.168.0.155:11434/v1"
os.environ["OPENAI_API_KEY"] = "ollama"

from evocua_agent import EvoCUAAgent   # our patched copy at repo root (evocua/ stays pristine)   # noqa: E402  # type: ignore[import]
from pico_env import PicoEnv                            # noqa: E402
import verify                                           # noqa: E402

# ---- harness-level (not model) knobs ----
SETTLE = 5.0          # = repo sleep_after_execution
MAX_STEPS = 50        # = repo max_steps (model budget)
ABORT_STEPS = 18      # early-stop for hopeless flailing on this microtask (time only)
REPS = 1          # SMOKE: 1 rep per config to validate the official-agent integration
# robustness hooks (see FINDINGS_2026-06-18_rootcause.md §C)
MAX_EMPTY_STREAK = 3  # k consecutive zero-action steps -> abort (a parse/format regression
                      # can no longer silently burn the whole step budget)
RECOVER_TRIES = 2     # on a false-positive terminate, inject ONE corrective turn and keep
                      # going, up to this many times, instead of ending the run as success
RECOVER_EXTRA_STEPS = 6  # extra time-budget granted to ABORT_STEPS per recovery attempt
# -----------------------------------------

TASKS = [
    {"name": "7x8+5", "instruction": "Using the open Calculator, compute 7 × 8 + 5", "expected": "61"},
]

# Each config = a full agent operating point. S2 = tool-calling (relative/32);
# S1 = structured reasoning (qwen25/28, raw image, big max_tokens). One var at a time.
CONFIGS = [
    {"label": "q5clean-S2-h4", "model": "evocua-8b-q5-clean", "prompt_style": "S2",
     "coordinate_type": "relative", "resize_factor": 32, "max_history_turns": 4,
     "max_tokens": 2048, "temperature": 0.01},
    # NOTE: S1 deliberately omitted. Per upstream, S1 is the trajectory-generation prompt
    # format (prompts.py line 53) — it lacks the action vocabulary, GUI-operation guidance,
    # and coordinate-frame declaration that live only in the S2 prompts. The released
    # checkpoint is served/evaluated as EvoCUA-S2; S2 is the only deployment mode here.
]

BATCH_DIR = os.path.join("runs", "probe_" + time.strftime("%Y%m%d_%H%M%S"))

OP_COL_X = 651
OPERATOR_WORDS = [("addition", "+"), ("plus", "+"), ("add", "+"),
                  ("subtraction", "-"), ("minus", "-"), ("subtract", "-"),
                  ("multiplication", "*"), ("multipl", "*"), ("times", "*"),
                  ("division", "/"), ("divi", "/"), ("equals", "="), ("equal", "=")]


def extract_action_text(response, style):
    """Pull just the action sentence (not the S1 Thought) for operator classification."""
    if not response:
        return ""
    if style == "S1":
        m = re.search(r"#{1,2}\s*Action\s*:?\s*\n+(.*?)(?=\n#{1,2}\s|\Z)", response, re.S)
        return (m.group(1).strip() if m else response)
    m = re.search(r"(?im)^\s*Action:\s*(.*)$", response)
    return (m.group(1).strip() if m else response)


def classify_operator(text):
    s = (text or "").lower()
    for word, sym in OPERATOR_WORDS:
        if word in s:
            return sym
    return None


def extract_click_xy(cmd):
    m = re.search(r"pyautogui\.(?:click|moveTo|doubleClick|tripleClick|rightClick)\(\s*"
                  r"(?:x\s*=\s*)?(-?\d+)\s*,\s*(?:y\s*=\s*)?(-?\d+)", str(cmd))
    return (int(m.group(1)), int(m.group(2))) if m else None


def corrective_instruction(base, display_read, expected):
    """Generic, OCR-cheap correction injected after a false-positive terminate. The agent
    folds it into the next turn's instruction; no upstream-agent changes required."""
    return (f"{base}\n\nIMPORTANT: A previous attempt ended with the display showing "
            f"{display_read!r}, which is NOT the correct answer ({expected!r}). The task is "
            f"NOT complete. Clear the display if needed, redo the calculation, verify the "
            f"display shows {expected!r}, and only then terminate.")


def run_one(agent, env, cfg, task, outdir):
    os.makedirs(outdir, exist_ok=True)
    agent.reset()
    obs = env.reset({"instruction": task["instruction"]})
    steps, recent = [], []
    finished, term = False, None
    instr = task["instruction"]          # may be replaced by a corrective on recovery
    expected = task.get("expected")
    # robustness-hook state
    dropped_actions = 0                  # non-terminal steps that parsed to ZERO actions
    empty_streak = 0                     # consecutive such steps (stuck detector)
    recover_left = RECOVER_TRIES         # remaining false-positive recovery attempts
    recovered = 0                        # how many corrective turns were injected
    abort_at = ABORT_STEPS               # grows when we grant recovery budget
    verified, display_read, term_status = None, None, None
    self_report = None                   # PATCH(answer-channel): model's claimed answer at terminate
    answers = []                         # PATCH(answer-channel): standalone answers/questions, if any
    aborted = None

    for it in range(MAX_STEPS):
        try:
            response, actions = agent.predict(instr, obs)
        except Exception as e:
            steps.append({"i": it, "error": str(e)})
            print(f"  [{it}] PREDICT ERROR: {e}")
            break

        action_text = extract_action_text(response, cfg["prompt_style"])
        op = classify_operator(action_text)

        # HOOK 1: dropped-action assertion. A non-empty model response that parses to
        # ZERO pyautogui actions means a format/parse problem ate the step (the exact
        # failure the tool_call-normalization fix targets). Never silently no-op it.
        # PATCH(answer-channel): a step that produced a model answer is communicative, not a
        # dropped action -- don't count it as empty (it also always carries a token).
        is_empty = not actions and not getattr(agent, "last_answer", None)
        if is_empty:
            dropped_actions += 1
            empty_streak += 1
            steps.append({"i": it, "op": op, "actions": [], "coord": None,
                          "dropped_action": True, "empty_streak": empty_streak,
                          "raw_head": (response or "")[:160]})
            print(f"  [{it}] !! ZERO ACTIONS PARSED (dropped #{dropped_actions}, "
                  f"streak {empty_streak}) — response head: {(response or '')[:80]!r}")
            # HOOK 2: stuck detector — k empty steps in a row -> abort, don't burn budget.
            if empty_streak >= MAX_EMPTY_STREAK:
                aborted = "empty_action_streak"
                print(f"  {empty_streak} empty steps in a row -> abort")
                steps.append({"i": it, "aborted": aborted}); break
            continue
        empty_streak = 0

        last_xy, done, step_answer = None, False, None
        for action in actions:
            obs, _, d, info = env.step(action, SETTLE)
            if action == "ANSWER" or info.get("answer"):
                # PATCH(answer-channel): standalone agent->user turn. Record, stay non-terminal.
                step_answer = getattr(agent, "last_answer", None)
                if step_answer is not None:
                    answers.append({"i": it, "text": step_answer})
                continue
            xy = extract_click_xy(action)
            if xy:
                last_xy = xy
            if d:
                done = True
                term = "DONE" if info.get("done") else ("FAIL" if info.get("fail") else None)
                break

        rec = {"i": it, "op": op, "actions": actions, "coord": last_xy,
               "action_text": action_text[:120]}
        if step_answer is not None:
            rec["answer"] = step_answer
        if op and last_xy:
            rec["op_in_operator_col"] = bool(last_xy[0] >= OP_COL_X)
        steps.append(rec)
        tag = ""
        if op and last_xy:
            tag = f"   <op {op} x={last_xy[0]} {'OPcol' if last_xy[0] >= OP_COL_X else 'DIGITcol'}>"
        print(f"  [{it}] {actions}{tag}")

        if done:
            # PATCH(answer-channel): the model's own claimed answer at terminate (if any),
            # captured alongside the OCR'd display so a self-report-vs-truth metric is free.
            if getattr(agent, "last_answer", None) is not None:
                self_report = agent.last_answer
            if term == "DONE":
                # HOOK 3: verify-before-terminate -> RECOVER. Check the display at the
                # moment of terminate; if it's a false positive and we still have
                # recovery budget, inject a corrective turn and KEEP GOING instead of
                # ending the run as a (false) success.
                verified, display_read = verify.verify_terminate(
                    env.cam.read(), expected,
                    save=os.path.join(outdir, f"verify_step{it}.png"))
                rec["verify"] = {"verified": verified, "read": display_read}
                if verified is False and recover_left > 0:
                    recover_left -= 1
                    recovered += 1
                    abort_at += RECOVER_EXTRA_STEPS
                    instr = corrective_instruction(task["instruction"], display_read, expected)
                    print(f"  !! FALSE-POSITIVE terminate (display {display_read!r} != "
                          f"{expected!r}) -> inject correction, continue "
                          f"({recover_left} recovery attempts left)")
                    rec["recovered"] = True
                    term, done = None, False     # un-terminate; loop continues
                    continue
            finished = True
            break

        # loop guards
        if last_xy:
            recent = (recent + [last_xy])[-6:]
            if len(recent) >= 6:
                clusters = []
                for c in recent:
                    if not any(abs(c[0] - k[0]) < 15 and abs(c[1] - k[1]) < 15 for k in clusters):
                        clusters.append(c)
                if len(clusters) <= 2:
                    aborted = "reclick_loop"
                    print("  re-click loop -> abort"); steps.append({"i": it, "aborted": aborted}); break
        if it + 1 >= abort_at:
            aborted = "step_budget"
            print("  step budget -> abort"); steps.append({"i": it, "aborted": aborted}); break

    with open(os.path.join(outdir, "end_full.png"), "wb") as f:
        f.write(env.end_full_png())

    # Final classification. If the run ended on an accepted DONE we already have a
    # verify result; otherwise re-read the end frame so every finished run is scored.
    if term == "DONE":
        if verified is None and display_read is None:
            verified, display_read = verify.verify_terminate(
                env.cam.read(), expected, save=os.path.join(outdir, "verify_crop.png"))
        if verified is True:
            term_status = "success"
        elif verified is False:
            term_status = "false_positive"   # exhausted recovery and still wrong
            print(f"  !! VERIFY FAILED (final): display {display_read!r} != expected "
                  f"{expected!r}  -> FALSE-POSITIVE terminate")
        else:
            term_status = "done_unverified"   # OCR unavailable where the loop runs
            print("  (verify skipped: tesseract not available here; scored later in sandbox)")
    elif aborted:
        term_status = aborted
    else:
        term_status = term  # FAIL / None

    # PATCH(answer-channel): does the model's claimed answer match the OCR'd truth?
    # None when we have no claim or no OCR; otherwise a clean self-report-accuracy signal.
    self_report_matches = None
    if self_report is not None and display_read is not None:
        self_report_matches = (str(self_report).strip() == str(display_read).strip())

    record = {"label": cfg["label"], "prompt_style": cfg["prompt_style"],
              "task": task["name"], "finished": finished, "term": term,
              "term_status": term_status, "verified": verified, "display_read": display_read,
              "self_report": self_report, "self_report_matches": self_report_matches,
              "answers": answers,
              "dropped_actions": dropped_actions, "recovered": recovered, "aborted": aborted,
              "iters": len(steps), "steps": steps}
    with open(os.path.join(outdir, "run.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    return record


def main():
    os.makedirs(BATCH_DIR, exist_ok=True)
    env = PicoEnv(cam_index=0, screen_size=(1920, 1080), show=False)  # HDMI passthrough on its own monitor; no preview window
    print(f"batch -> {BATCH_DIR}\n")
    manifest = []
    try:
        for cfg in CONFIGS:
            agent = EvoCUAAgent(
                model=cfg["model"], max_tokens=cfg["max_tokens"], top_p=0.9,
                temperature=cfg["temperature"], action_space="pyautogui",
                observation_type="screenshot", max_steps=MAX_STEPS,
                prompt_style=cfg["prompt_style"], max_history_turns=cfg["max_history_turns"],
                screen_size=(1920, 1080), coordinate_type=cfg["coordinate_type"],
                resize_factor=cfg["resize_factor"],
            )
            for task in TASKS:
                for rep in range(REPS):
                    name = f"{cfg['label']}__{task['name']}__rep{rep:02d}"
                    print(f"=== {name} ===")
                    outdir = os.path.join(BATCH_DIR, name)
                    rec = run_one(agent, env, cfg, task, outdir)
                    ops = [s for s in rec["steps"] if s.get("op")]
                    entry = {
                        "dir": name, "model": cfg["label"], "history": cfg["max_history_turns"],
                        "prompt_style": cfg["prompt_style"], "task": task["name"],
                        "expected": task["expected"], "rep": rep, "finished": rec["finished"],
                        "term": rec["term"], "term_status": rec.get("term_status"),
                        "verified": rec.get("verified"), "display_read": rec.get("display_read"),
                        "self_report": rec.get("self_report"),                 # PATCH(answer-channel)
                        "self_report_matches": rec.get("self_report_matches"), # PATCH(answer-channel)
                        "n_answers": len(rec.get("answers") or []),            # PATCH(answer-channel)
                        "dropped_actions": rec.get("dropped_actions"),
                        "recovered": rec.get("recovered"), "aborted": rec.get("aborted"),
                        "iters": rec["iters"],
                        "operator_clicks": [
                            {"op": s["op"], "x": (s.get("coord") or [None])[0],
                             "in_op_col": s.get("op_in_operator_col")} for s in ops],
                    }
                    manifest.append(entry)
                    with open(os.path.join(BATCH_DIR, "manifest.json"), "w", encoding="utf-8") as f:
                        json.dump(manifest, f, indent=2, ensure_ascii=False)
                    print(f"  -> finished={rec['finished']} term={rec['term']} "
                          f"status={rec.get('term_status')} read={rec.get('display_read')} "
                          f"iters={rec['iters']}\n")
    except KeyboardInterrupt:
        # Ctrl+C: stop cleanly. The manifest is written after every rep, so partial
        # results are already on disk; finally releases the camera + Pico.
        print("\n[interrupted] Ctrl+C — stopping, releasing hardware...")
    finally:
        env.close()
        print(f"\nBatch complete -> {BATCH_DIR}")
        print("Score: python3 score_batch.py " + BATCH_DIR.replace('\\', '/') + "   (in sandbox)")


if __name__ == "__main__":
    main()
