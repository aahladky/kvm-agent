"""
agent_loop_holo.py — REPL-driven capture->ground->act loop for Holo3.1, built around
kvm_agent.models.holo (structured-output mechanism, native-verbatim line 2026-07-21 --
see that file's module docstring for what "verbatim" covers and the disclosed deviations).

VERBATIM-NATIVE CHANGES vs the 2026-07-19 structured-output line:
    - Steps are BATCHES: the model's tool_calls array is executed sequentially on the one
      desktop (native semantics: calls see each other's effects, only the batch's final
      screenshot goes back, and a halted batch skips remaining calls on error).
    - Execution results go back through native's <tool_output tool="..."> user-message
      channel (the 2026-07-19 capture's "native has no result channel" claim was wrong --
      the channel is in the runtime binary's constants). Payload is OUR camera-based
      frame-diff signal with magnitude+region ("report WHAT changed", the 2026-07-21
      follow-up), not a hardcoded "ok" and not a bare changed/unchanged binary.
    - Native's tool vocabulary is executed directly: double_click, move_to, drag_to (from
      the CURRENT cursor position -- tracked here), scroll with scroll_size wheel clicks,
      hotkey (keys list + repeat_count), hold_and_tap, update_plan (harness-side plan
      bookkeeping). No more '+'-joined press_key string, no more drag with explicit start.
    - Model input is Camera.model_input_jpeg() (JPEG, CFG.holo_model_input_res -- 1080
      native default, 720 A/B knob); diff/evidence frames stay full-res PNG.
    - Still NO injected retries/inputs (the 2026-07-19 contamination fix stands): the
      model judges success from the next observation via its own thought, per native's
      design. The frame-diff signal is reported to it, never acted on by host code.
      The wait_newer freshness floor (finding #6 pairing) is KEPT -- that is observation
      correctness (the after-frame must postdate the action), not input injection.

Modeled on live_ctl.py's proven propose-then-confirm shape (ground() proposes, do()
executes) so review-before-fire stays the default, per CLAUDE.md's "make failure loud"
discipline -- run()'s CONFIRM_FIRST gates the first N steps with a keypress preview, and
a stuck-detector (STUCK_LIMIT consecutive dropped/error steps) aborts instead of burning
the step budget.

Typical:
    from agent_loop_holo import *
    boot()                                  # open camera + appliance HID
    cap()                                   # grab a fresh frame -> scratch/_dbg/live.png
    ground("click the Save button")         # calls Holo, proposes a step, does NOT execute
    mark()                                  # eyeball the crosshair before firing
    do()                                    # execute the proposed step's action(s)
    run("open Notepad and type hello", max_steps=10)   # closed multi-step loop w/ history
"""
import os
import time
from io import BytesIO

from PIL import Image

from kvm_agent.config import CFG
from kvm_agent.hardware.appliance import ApplianceError
from kvm_agent.hardware.env import PicoEnv, frame_diff_detail, wait_until_stable
from kvm_agent.hardware.target import verify_hid
from kvm_agent.instrumentation import RunRecorder
from kvm_agent.models.holo import (
    _error_step, call_holo, call_holo_full, jpeg_bytes_to_data_url, observation_message,
    tool_output_message, trim_to_last_n_images,
)

MAX_HISTORY_IMAGES = CFG.holo_history_images   # default 3 = native max_images (see config)

DBG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch", "_dbg")
os.makedirs(DBG, exist_ok=True)

CONFIRM_FIRST = 5   # gate the first N steps of run() with a keypress preview
STUCK_LIMIT = 3     # k consecutive dropped/error steps -> abort (make-failure-loud guard)
# Single knob since 2026-07-21 (review P3: 3.0 lived hardcoded in three places) --
# calibration notes live on the CFG field.
FRAME_CHANGE_THRESHOLD = CFG.frame_change_threshold
NO_PROGRESS_LIMIT = 4   # k consecutive executed steps with no visible change OR the identical
                        # action repeated -> abort as "no progress" (flaw #9). small_target_tray
                        # clicked ~the same coord 6x and burned the whole budget undetected.
STALL_GRACE_S = 2.0     # extra wait_newer grace after the settle-budget freshness wait times
                        # out, before declaring a capture stall -- the settle budget (1.5s)
                        # doubles as the freshness timeout and is tight for a busy pipeline
                        # (review 2026-07-21 P0-3 decision: escalate, don't raise).
STALL_ABORT_LIMIT = 3   # k consecutive stalled-capture steps -> abort. A rig fault, NOT model
                        # behavior, so it is deliberately not gated by no_progress_abort.

ENV = None
LAST = {"png": None, "step": None, "history": None}
# Current cursor position in REAL screen pixels, tracked across every pointer action we
# fire. Needed to execute native's drag_to_desktop, which drags FROM the current cursor
# position rather than a model-specified start. None until the first pointer action.
CURSOR = {"pos": None}
# Current update_plan state (the model's goal list as last submitted). Harness-side
# bookkeeping only; surfaced in run logs.
PLAN = {"goals": []}


def boot(verify=True):
    """Open camera + appliance HID, sync the bridge's click scale to the pixel space
    this loop sends coordinates in, and run the camera-verified HID gate.

    The gate (review 2026-07-21 P0-4): the bridge probe's online flags can LIE (the
    I2 half-dead-composite class), and until now only the battery ran verify_hid --
    every REPL session drove an unverified channel. One non-interactive attempt
    (~10s); failure releases the hardware and raises with the gate's diagnosis, so
    boot() stays re-runnable after a replug. verify=False skips it for fast iteration
    on a just-verified channel; the battery passes False because it runs its own
    interactive replug-loop gate per task."""
    global ENV
    if ENV is None:
        ENV = PicoEnv(cam_index=CFG.cam_index, screen_size=CFG.screen_size, show=False)
        # Nothing else ever pushes /hid/set_screen (review 2026-07-21 P0-1), so without
        # this the bridge scales clicks from its process-lifetime default and a
        # resolution mismatch stretches every click silently.
        ENV.r4.set_screen(ENV.screen_width, ENV.screen_height)
        if verify:
            ok, detail = verify_hid(ENV.r4, ENV.cam,
                                    screen=(ENV.screen_width, ENV.screen_height),
                                    attempts=1)
            if not ok:
                shutdown()   # leave a re-runnable state, not a half-alive ENV
                raise RuntimeError(
                    f"HID gate failed at boot: {detail}. Replug the Pico and boot() "
                    f"again, or boot(verify=False) to skip the gate deliberately.")
    print(f"[boot] ready. holo target=local ({CFG.holo_local_url}, model={CFG.holo_model})")
    return True


def _frame_png():
    """Full-res PNG frame for diffing/evidence (PicoEnv.observe() is full-res PNG since
    the native-verbatim split -- model input has its own JPEG path)."""
    return ENV.observe()["screenshot"]


def _model_input_data_url():
    """JPEG data URL at CFG.holo_model_input_res for the model (native-style input)."""
    return jpeg_bytes_to_data_url(ENV.cam.model_input_jpeg())


def cap(name="live"):
    """Grab a fresh frame, save PNG to scratch/_dbg/<name>.png, return the path."""
    png = _frame_png()
    LAST["png"] = png
    p = os.path.join(DBG, f"{name}.png")
    with open(p, "wb") as f:
        f.write(png)
    w, h = Image.open(BytesIO(png)).size
    print(f"[cap] {w}x{h} -> {p}")
    return p


def ground(instruction, target="local"):
    """ONE Holo call against the CURRENT frame. Proposes a step (a LIST of actions);
    does NOT execute -- review it (mark() to eyeball the crosshair) then call do()."""
    png = _frame_png()
    LAST["png"] = png
    # Projection targets REAL screen pixels, not the model-input image: Holo outputs
    # [0,1000] normalized coords, so the image size only matters as the projection basis --
    # and that basis must be the screen the HID moves on.
    w, h = ENV.screen_width, ENV.screen_height
    t0 = time.time()
    step = call_holo(instruction, _model_input_data_url(), w, h, target=target)
    dt = time.time() - t0
    LAST["step"] = step
    print(f"[ground {dt:.1f}s] {instruction!r} -> {step}")
    return step


def mark(name="mark"):
    """Save the current frame with a crosshair at the first coordinate-bearing action of
    the proposed step, to eyeball grounding before firing."""
    step = LAST.get("step") or {}
    action = next((a for a in step.get("actions", []) if a.get("coordinate")), None)
    if LAST["png"] is None or not action:
        print("[mark] need a ground() proposal with a coordinate first")
        return None
    import cv2
    import numpy as np
    arr = cv2.imdecode(np.frombuffer(LAST["png"], np.uint8), cv2.IMREAD_COLOR)
    # LAST["png"] is full-res screen pixels, action coords are real screen pixels.
    x, y = (int(v) for v in action["coordinate"])
    cv2.drawMarker(arr, (x, y), (0, 0, 255), cv2.MARKER_CROSS, 40, 3)
    cv2.circle(arr, (x, y), 22, (0, 0, 255), 2)
    p = os.path.join(DBG, f"{name}.png")
    cv2.imwrite(p, arr)
    print(f"[mark] {x},{y} -> {p}")
    return p


def _execute(action, settle_s=1.5):
    """Fire ONE normalized Holo action dict via the appliance, EXACTLY as the model
    decided -- no injected extra inputs, no retries (the 2026-07-19 contamination fix:
    an auto-retry once retyped a successfully-delivered `type` and corrupted draft.txt;
    every run measured with injection active was measuring "Holo + our injection," not
    Holo. Native's loop has no host-side execution-retry heuristic: the model sees the
    next screenshot and judges via its own `thought`). Maps onto env.r4. CURSOR["pos"] is
    updated on every pointer action so drag_to can start from the true current position.

    The wait_newer freshness floor (finding #6 pairing) is KEPT: the capture pipeline
    must advance PAST the fire before settling, so a later diff frame can never be one
    captured before the action landed. That is observation correctness, not injection.

    Returns True when the freshness floor was VIOLATED (capture stalled past the grace
    window, or delivered no frames at all) -- the caller owns what to do with a diff
    that may predate the action. False on a healthy capture.
    """
    kind = action.get("action")
    seq0 = ENV.cam.seq

    if kind in ("left_click", "double_click"):
        x, y = (int(v) for v in action["coordinate"])
        ENV.r4.move(x, y)
        ENV.r4.click()
        if kind == "double_click":
            ENV.r4.click()
        CURSOR["pos"] = (x, y)
    elif kind == "move_to":
        x, y = (int(v) for v in action["coordinate"])
        ENV.r4.move(x, y)
        CURSOR["pos"] = (x, y)
    elif kind == "drag_to":
        # Native's drag_to_desktop drags FROM the current cursor position. If no pointer
        # action has fired yet this session there is no tracked position -- loud no-op
        # rather than dragging from a guessed origin.
        if CURSOR["pos"] is None:
            print("[execute] drag_to with no tracked cursor position -- no-op")
        else:
            x1, y1 = CURSOR["pos"]
            x2, y2 = (int(v) for v in action["coordinate"])
            # Re-assert the start before pressing: CURSOR tracks every pointer action we
            # fire, but the PHYSICAL cursor can drift target-side (review 2026-07-21 P1-8).
            ENV.r4.move(x1, y1)
            ENV.r4.down()
            ENV.r4.move(x2, y2)
            ENV.r4.up()
            CURSOR["pos"] = (x2, y2)
    elif kind == "type":
        ENV.r4.type(action.get("text", ""))
        if action.get("press_enter"):
            ENV.r4.key("enter")
    elif kind == "hotkey":
        spec = "+".join(action.get("keys", []))
        for _ in range(max(1, int(action.get("repeat_count", 1)))):
            ENV.r4.combo(spec)
    elif kind == "hold_and_tap":
        # Our combo() holds all listed keys and taps the last; tapping each tap_key in
        # sequence means one combo per tap. The held keys are re-asserted per tap rather
        # than held continuously across the sequence -- closest available mapping (the
        # bridge has no key-hold primitive; see holo.py deviation #1).
        hold = action.get("hold_keys", [])
        for tap in action.get("tap_keys", []):
            ENV.r4.combo("+".join(hold + [tap]))
    elif kind == "scroll":
        direction = action.get("direction")
        ticks = min(100, max(1, int(action.get("scroll_size", 3))))
        # Native places the cursor at (x, y) FIRST, then turns the wheel there -- the
        # wheel turns wherever the cursor sits, and an untargeted scroll can no-op
        # forever (flaw #10, scroll_to_about 2026-07-18).
        target = action.get("coordinate")
        if target:
            ENV.r4.move(int(target[0]), int(target[1]))
            CURSOR["pos"] = (int(target[0]), int(target[1]))
            time.sleep(0.3)
        if direction == "up":
            ENV.r4.scroll(ticks)
        elif direction == "down":
            ENV.r4.scroll(-ticks)
        else:
            # v5 firmware wheel is single-axis vertical only -- left/right have no real
            # mapping. No-op, loud, rather than a wrong guess.
            print(f"[execute] scroll direction={direction!r} not supported by current "
                  f"firmware (vertical wheel only) -- no-op")
    elif kind == "update_plan":
        PLAN["goals"] = action.get("goals", [])
        running = [g.get("title") for g in PLAN["goals"] if g.get("status") == "running"]
        print(f"[execute] plan updated ({len(PLAN['goals'])} goals, running: {running})")
        return False  # no screen effect -> no settle wait, nothing to stall
    elif kind == "finished":
        pass    # nothing to execute; run() handles this as loop-terminal
    else:
        print(f"[execute] unknown action kind {kind!r} -- no-op")

    # Freshness floor: the capture pipeline must advance PAST the fire before settling.
    # A violated floor is RETURNED, not swallowed (review 2026-07-21 P0-3): the caller
    # must know the next diff may predate the action -- the finding-#6 class the seq
    # pairing exists to prevent.
    stalled = False
    try:
        ENV.cam.wait_newer(seq0, timeout_s=settle_s)
    except TimeoutError:
        try:
            ENV.cam.wait_newer(seq0, timeout_s=STALL_GRACE_S)
        except TimeoutError:
            stalled = True
            print(f"[execute] WARNING: capture stalled -- no frame newer than seq={seq0} "
                  f"within {settle_s + STALL_GRACE_S}s; diffs may predate this action")
    # Smart settle (2026-07-18): proceed the moment the UI stops changing, up to settle_s.
    if wait_until_stable(ENV.cam.read, settle_s) == "no_frames":
        stalled = True
        print("[execute] WARNING: capture delivered no frames during settle")
    return stalled


# Tile-diff metric: canonical home is kvm_agent/hardware/env.py since 2026-07-21
# (review P3 -- it lived here AND there as hand-copies). These names stay for callers.
_frame_diff_detail = frame_diff_detail


def _frame_diff_score(png_a, png_b):
    return frame_diff_detail(png_a, png_b)[0]


def _frame_changed(png_a, png_b, threshold=None):
    threshold = FRAME_CHANGE_THRESHOLD if threshold is None else threshold
    return frame_diff_detail(png_a, png_b)[0] > threshold


def do(s=1.5):
    """Execute the proposed step's action(s) via the appliance."""
    step = LAST.get("step") or {}
    actions = step.get("actions", [])
    if not actions:
        print("[do] nothing proposed")
        return
    for action in actions:
        _execute(action, settle_s=s)
        print(f"[do] {action}")


def run(instruction, max_steps=10, target="local", confirm_first=None, record=True, tag="run",
        no_progress_abort=True):
    """Multi-step closed loop, native semantics: ground (against accumulated history) ->
    confirm (first N steps) -> execute the batch sequentially -> re-capture -> thread this
    step's observation + the assistant JSON turn + one <tool_output> per executed call
    into history -> repeat.

    History layout (native's, confirmed against the runtime binary's chat-mapper
    constants and hub.hcompany.ai/agent-loop): each successful step appends
      {"role": "user", "content": [<observation>+image+</observation>]}   (this step's own)
      {"role": "assistant", "content": "<the JSON string the model returned>"}
      {"role": "user", "content": "<tool_output tool=...>"}               (one per executed call)
    then trims to the last MAX_HISTORY_IMAGES screenshots (default 3 = native). The task
    instruction is sent ONLY on step 0's observation turn -- later turns carry it via
    history. Only the batch's FINAL screenshot is re-observed by the model (native's own
    batching rule). Steps that error (dropped/unparseable JSON) are NOT threaded into
    history -- referencing a malformed turn back to the model would confuse it more than
    a clean retry. The response_format schema constraint makes these rare.

    confirm_first defaults to CONFIRM_FIRST; pass 0 to run unattended.

    record (default True) writes every step's pre-action frame, raw message, parsed step,
    token usage, and wall time to CFG.runs_dir/<tag>_<timestamp>/ via RunRecorder, plus a
    summary.json at the end. tag names the run directory (e.g. the task id in a battery).

    Returns {"finished": bool, "answer_text": str} (flaw #11: the battery runner needs
    the model's actual final answer text to tell an honest refusal apart from silently
    exhausting the step budget). answer_text is the `answer` tool's content; "" on every
    non-finished return (stuck limit / no-progress abort / max_steps).
    """
    confirm_first = CONFIRM_FIRST if confirm_first is None else confirm_first
    history = []
    LAST["history"] = history
    stuck = 0
    frozen = 0          # consecutive executed steps with no visible screen change
    click_repeat = 0    # consecutive steps whose last click landed in ~the same spot
    last_click = None
    stall_streak = 0    # consecutive steps whose capture stalled (rig-fault guard)
    recorder = RunRecorder(tag, instruction, target=target,
                            meta={"max_steps": max_steps,
                                  "screen_size": (ENV.screen_width, ENV.screen_height),
                                  "model_input_res": CFG.holo_model_input_res,
                                  "history_images": MAX_HISTORY_IMAGES}) if record else None
    for step_i in range(max_steps):
        png = _frame_png()
        LAST["png"] = png
        w, h = ENV.screen_width, ENV.screen_height
        data_url = _model_input_data_url()
        step_instruction = instruction if step_i == 0 else ""
        t0 = time.time()
        try:
            step, message, usage = call_holo_full(step_instruction, data_url, w, h, target=target,
                                                  history=history, max_history_images=MAX_HISTORY_IMAGES)
        except Exception as e:
            # A transport/API failure (timeout, refused, 5xx) must end THIS TASK with a
            # recorded verdict, not propagate and abort every remaining battery task
            # (review 2026-07-21 P0-2; logs/holo_requests.jsonl still has the raw
            # failure). Routed through the dropped-step path below: recorder logs it,
            # stuck-limit finishes with summary.json.
            step, message, usage = _error_step(f"model call failed: {e}", {}), {}, None
        dt = time.time() - t0
        LAST["step"] = step
        print(f"[run {dt:.1f}s] step {step_i}: {step.get('note')!r} | {step.get('actions')}")

        if step.get("error"):
            stuck += 1
            if recorder:
                recorder.log_step(step_i, png, message, step, usage, dt, executed=False)
            print(f"[run] step {step_i}: dropped step ({stuck}/{STUCK_LIMIT})")
            if stuck >= STUCK_LIMIT:
                print("[run] stuck limit hit -- aborting")
                if recorder:
                    recorder.finish(False, note="stuck limit hit")
                return {"finished": False, "answer_text": ""}
            continue
        stuck = 0
        actions = step["actions"]

        if step_i < confirm_first:
            input(f"[run] step {step_i}: about to execute {actions} -- Enter to confirm...")

        # Execute the batch SEQUENTIALLY (native: one desktop, calls see each other's
        # effects; on error the remaining calls are skipped). One <tool_output> per
        # executed call carries our frame-diff what-changed signal.
        tool_outputs = []
        answer_text = None
        exec_error = False
        step_changed = False
        step_stalled = False
        for action in actions:
            kind = action.get("action")
            tool_name = {"left_click": "click_desktop", "double_click": "double_click_desktop",
                         "type": "write_desktop", "scroll": "scroll_desktop",
                         "drag_to": "drag_to_desktop", "move_to": "move_to_desktop",
                         "hotkey": "hotkey_desktop", "hold_and_tap": "hold_and_tap_key_desktop",
                         "update_plan": "update_plan", "finished": "answer"}.get(kind, str(kind))
            before = _frame_png()
            try:
                call_stalled = _execute(action)
            except ApplianceError as e:
                # A rejected/undeliverable action (e.g. a model-invented key name ->
                # bridge 502) halts the batch (native semantics) but must not kill the
                # run: count it like a dropped step (2026-07-21: 'winkey' crashed a
                # battery run at step 1 before this existed).
                print(f"[run] step {step_i}: exec error ({e}) -- batch halted")
                tool_outputs.append(tool_output_message(tool_name, f"Error: {e}. Remaining calls in this step were not executed."))
                exec_error = True
                break
            if kind == "update_plan":
                tool_outputs.append(tool_output_message(tool_name, "Plan updated."))
                continue
            after = _frame_png()
            score, region = _frame_diff_detail(before, after)
            changed = score > FRAME_CHANGE_THRESHOLD
            step_changed = step_changed or changed
            result = (f"Executed. Screen changed (max tile diff {score:.1f}, region {region})."
                      if changed else
                      f"Executed. Screen did not visibly change (max tile diff {score:.1f}).")
            if call_stalled:
                # The model must not trust a diff whose after-frame may predate the
                # action (review 2026-07-21 P0-3: this used to be a swallowed print).
                step_stalled = True
                result += (" WARNING: capture stalled after this action -- this result "
                           "may reflect a pre-action frame.")
            tool_outputs.append(tool_output_message(tool_name, result))
            if kind == "finished":
                answer_text = action.get("text", "")
                break  # terminal: nothing after answer executes

        if recorder:
            recorder.log_step(step_i, png, message, step, usage, dt, executed=not exec_error,
                              stalled=step_stalled)

        if step.get("note"):
            print(f"[run] note: {step['note']!r}")

        if exec_error:
            stuck += 1
            if stuck >= STUCK_LIMIT:
                print("[run] stuck limit hit (exec errors) -- aborting")
                if recorder:
                    recorder.finish(False, note="stuck limit hit (exec errors)")
                return {"finished": False, "answer_text": ""}
            continue

        # Thread this step into history: own observation (without re-rendering the
        # instruction -- it appears on the live call only) + assistant JSON + tool_outputs.
        history.append(observation_message(data_url, step_instruction))
        history.append({"role": "assistant", "content": message.get("content") or ""})
        history.extend(tool_outputs)
        trim_to_last_n_images(history, n=MAX_HISTORY_IMAGES)

        if answer_text is not None:
            print(f"[run] finished: {answer_text!r}")
            if recorder:
                recorder.finish(True, note=answer_text)
            return {"finished": True, "answer_text": answer_text}

        # Rig-fault guard (review 2026-07-21 P0-3): a repeatedly stalled capture means no
        # result can be trusted. Deliberately NOT gated by no_progress_abort -- that flag
        # masks MODEL-behavior guards for benchmarks; this is our pipeline failing.
        stall_streak = stall_streak + 1 if step_stalled else 0
        if stall_streak >= STALL_ABORT_LIMIT:
            print(f"[run] capture stalled {stall_streak} consecutive steps -- aborting (rig fault)")
            if recorder:
                recorder.finish(False, note="capture pipeline stalled")
            return {"finished": False, "answer_text": ""}

        # no-progress guards (flaw #9): abort instead of silently burning the budget.
        # (a) screen frozen -- consecutive executed steps with no visible change; (b) clustered
        # repeated clicks -- consecutive steps whose last click landed within ~25px (the
        # small_target_tray case, where clicks toggled a flyout so 'changed' was True but
        # nothing advanced).
        # A plan-only batch has no screen effect by design: it neither proves progress
        # nor indicates a freeze, so it leaves the counter untouched (review 2026-07-21
        # P1-7: four planning-only steps used to trip the "screen frozen" abort).
        if any(a.get("action") != "update_plan" for a in actions):
            frozen = frozen + 1 if not step_changed else 0
        step_clicks = [a for a in actions if a.get("action") in ("left_click", "double_click") and a.get("coordinate")]
        if step_clicks:
            c = step_clicks[-1]["coordinate"]
            if last_click and abs(c[0] - last_click[0]) <= 25 and abs(c[1] - last_click[1]) <= 25:
                click_repeat += 1
            else:
                click_repeat = 0
            last_click = c
        else:
            click_repeat = 0
            last_click = None
        if no_progress_abort and (frozen >= NO_PROGRESS_LIMIT or click_repeat >= NO_PROGRESS_LIMIT):
            reason = (f"screen frozen {frozen} steps" if frozen >= NO_PROGRESS_LIMIT
                      else f"same click x{click_repeat + 1}")
            print(f"[run] no progress ({reason}) -- aborting")
            if recorder:
                recorder.finish(False, note="no progress: " + reason)
            return {"finished": False, "answer_text": ""}
    print("[run] max_steps reached without finishing")
    if recorder:
        recorder.finish(False, note="max_steps reached")
    return {"finished": False, "answer_text": ""}


def shutdown():
    """Close camera + appliance cleanly (leaves the firmware accept() healthy)."""
    global ENV
    if ENV is not None:
        try:
            ENV.close()
        except Exception as e:
            print("[shutdown] env close err:", e)
    ENV = None
    print("[shutdown] hardware released")
