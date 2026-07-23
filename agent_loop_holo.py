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
    - Pre-fire TOCTOU guard (2026-07-22): the first screen-affecting coordinate action
      of a batch is refused (never fired, batch halted, model re-observes) if the tile
      region around its target changed between the decision frame and the pre-fire
      frame -- the paint_line s09 race, where GNOME's async search re-flowed during the
      model's 18.8s think and the click activated the row that slid under it. Gating
      progression, not injecting action; see GUARD_KINDS / GUARD_REFUSE_LIMIT.
    - Postcondition verification (2026-07-23, roadmap Phase 2 slices D-b/D-c): run(verifier=,
      verify_mode=) optionally checks the model's own `finished` claim against the
      pixels via a stateless kvm_agent.models.base.Verifier -- separate from and blind
      to the actor's session history. "off" (default) is behaviorally and
      dict-return-shape IDENTICAL to pre-D-b run(); "shadow" records the verdict
      without changing control flow (this slice's whole point: measure live
      false-refusal/agreement before anything is allowed to gate on it); "gate" refuses
      an unsatisfied or unanswered finished claim, threads the oracle's evidence back to
      the actor, and aborts loudly after VERIFY_REFUSE_LIMIT refusals. See VERIFY_MODES.

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
from kvm_agent.hardware.env import (
    PicoEnv, frame_png_bytes, model_input_jpeg, png_to_model_input_jpeg, tile_means_png,
    tile_region_max_png, wait_until_stable,
)
from kvm_agent.instrumentation import RunRecorder
from kvm_agent.llm.serving import describe as describe_serving, serving_snapshot
from kvm_agent.models.base import StepDecision, Verdict
from kvm_agent.models.holo import (
    SYSTEM_PROMPT, HoloSession, call_holo, call_holo_full, jpeg_bytes_to_data_url,
)

MAX_HISTORY_IMAGES = CFG.holo_history_images   # default 3 = native max_images (see config)

DBG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch", "_dbg")
os.makedirs(DBG, exist_ok=True)

CONFIRM_FIRST = 5   # gate the first N steps of run() with a keypress preview
STUCK_LIMIT = 3     # k consecutive dropped/error steps -> abort (make-failure-loud guard)
# _frame_changed threshold on the tile-max metric — lives in CFG (the single home,
# 2026-07-21); see config.py for the 2026-07-18 calibration notes. This module-level
# name stays as the import-compatible alias (tests/test_frame_diff.py).
FRAME_CHANGE_THRESHOLD = CFG.frame_change_threshold
NO_PROGRESS_LIMIT = 4   # k consecutive executed steps with no visible change OR the identical
                        # action repeated -> abort as "no progress" (flaw #9). small_target_tray
                        # clicked ~the same coord 6x and burned the whole budget undetected.
# Pre-fire TOCTOU guard (2026-07-22, SESSION_2026-07-22 finding 2): the screen can
# re-flow during the model's ~15-20s think, so a click correct against the decision
# frame lands on whatever slid under it (paint_line s09: GNOME's async search re-flow).
# Before firing the FIRST screen-affecting coordinate action of a batch, tile-diff the
# region around the target between the decision frame and a fresh pre-fire frame;
# changed -> refuse to fire and re-observe. Gating progression, NOT injecting action
# (the 2026-07-19 anti-contamination rule). Later batch actions are unguarded by
# design: the model decided them anticipating its own earlier actions' effects, so no
# reference frame exists for "what it expected the screen to look like".
GUARD_KINDS = ("left_click", "double_click", "drag_to")   # drag_to: the drop target
GUARD_REFUSE_LIMIT = 3  # consecutive guard refusals -> abort: the target region is
                        # unstable across whole decision cycles (spinner/animation).
                        # Not a model failure -- never counted against STUCK_LIMIT.

# Postcondition verification (roadmap Phase 2 slices D-b/D-c, docs/PLAN_2026-07-22_phase2_
# subgoal_verification.md): the model's own `finished`/answer claim, checked against the
# pixels by a kvm_agent.models.base.Verifier -- a stateless oracle, never the actor's own
# session. "gate" refuses an unsatisfied OR unanswered claim and lets the actor
# re-observe; k refusals terminate as a distinct failed outcome.
VERIFY_MODES = ("off", "shadow", "gate")
VERIFY_REFUSE_LIMIT = 3

ENV = None
LAST = {"png": None, "step": None, "history": None}
# Current cursor position in REAL screen pixels, tracked across every pointer action we
# fire. Needed to execute native's drag_to_desktop, which drags FROM the current cursor
# position rather than a model-specified start. None until the first pointer action.
CURSOR = {"pos": None}
# Current update_plan state (the model's goal list as last submitted). Harness-side
# bookkeeping only; surfaced in run logs.
PLAN = {"goals": []}
# Serving-layer snapshot taken by boot() (kvm_agent.llm.serving). `checked` gates the
# per-run refresh in run(): only a session that booted through the serving preflight
# pays for (or records) one. Offline tests set ENV directly and never call boot(), so
# this stays False for them and the suite never touches the endpoint.
SERVING = {"checked": False}


def boot(verify=True, serving_check=True):
    """Open camera + appliance HID. Idempotent-ish; call once.

    serving_check (default True) records what is actually serving the model before the
    run starts (kvm_agent.llm.serving): reachable, configured, resident, and the launch
    params that shape what the model SEES (context, mmproj, --image-min-tokens, KV cache
    types). The model server lives outside this repo and nothing here used to look at
    it. Unlike the HID gate this one WARNS and never raises: the HID gate raises because
    clicking into a dead device corrupts silently, whereas every serving problem
    announces itself at the first model call anyway. `tools/serving_probe.py` is the
    fail-closed version for a preflight.

    verify (default True) runs the camera-verified HID gate (target.verify_hid) after
    bring-up: the bridge probe's kbd/mouse online flags can LIE (the I2 half-dead-HID
    class), so an unverified session clicks into the void silently. Previously only
    the battery gated (2026-07-21 review P0-4); every REPL/non-battery session ran
    unverified. On gate failure the env is torn down and boot raises. Pass
    verify=False only where a caller runs its own gate (the battery's interactive
    per-task replug loop)."""
    global ENV
    if ENV is None:
        ENV = PicoEnv(cam_index=CFG.cam_index, screen_size=CFG.screen_size, show=False)
    if serving_check:
        snap = serving_snapshot()
        SERVING.clear()
        SERVING.update(snap, checked=True)
        print(f"[boot] serving: {describe_serving(snap)}")
        if snap.get("reachable") and snap.get("configured") is False:
            print(f"[boot] WARNING: {CFG.holo_model!r} is not configured at "
                  f"{snap['endpoint']} -- every model call this session will fail")
        if snap.get("resident") and not (snap.get("params") or {}).get("has_mmproj", True):
            print("[boot] WARNING: the resident model has NO mmproj -- it cannot see "
                  "images, and every grounding decision will be blind")
    if verify:
        from kvm_agent.hardware.target import verify_hid
        ok, detail = verify_hid(ENV.r4, ENV.cam, screen=CFG.screen_size)
        print(f"[boot] hid gate: {detail}")
        if not ok:
            try:
                ENV.close()
            except Exception:
                pass
            ENV = None
            raise RuntimeError(
                f"[boot] HID gate failed: {detail} -- replug the Pico's USB at the "
                f"target (or power-cycle it) and retry, or call boot(verify=False) "
                f"to bypass the gate")
    print(f"[boot] ready. holo target=local ({CFG.holo_local_url}, model={CFG.holo_model})")
    return True


def _frame_png():
    """Full-res PNG frame for diffing/evidence (PicoEnv.observe() is full-res PNG since
    the native-verbatim split -- model input has its own JPEG path)."""
    return ENV.observe()["screenshot"]


def _model_input_data_url():
    """JPEG data URL at CFG.holo_model_input_res for the model (native-style input)."""
    return jpeg_bytes_to_data_url(ENV.cam.model_input_jpeg())


def _capture_step_frames():
    """ONE buffer read -> (evidence PNG, model-input data URL): both views of the
    SAME frame at the SAME instant (2026-07-21 second review #7 -- previously the
    evidence PNG and the model JPEG were two separate buffer reads: different
    instants on a changing screen, different resolutions, different encodings,
    while the recorder's header claims step_NN.png is the exact pre-decision
    frame)."""
    frame = ENV.cam.read()
    return (frame_png_bytes(frame),
            jpeg_bytes_to_data_url(model_input_jpeg(frame, CFG.holo_model_input_res)))


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

    Returns None for update_plan (no screen effect, early return) and otherwise a dict
    {"stalled": str|None, "settle": str|None, "noop": str|None} — `noop` is set when
    the action was NOT performed (unsupported/no-op), so run() can say so in the
    <tool_output> instead of reporting a misleading diff (2026-07-21 second review
    #11). See the freshness-floor note below for `stalled`/`settle`.

    The wait_newer freshness floor (finding #6 pairing) is KEPT: the capture pipeline
    must advance PAST the fire before settling, so a later diff frame can never be one
    captured before the action landed. That is observation correctness, not injection.
    seq0 is taken AFTER the last HID command returns (2026-07-21 second review #10):
    read at entry, frames arriving DURING the fire satisfied wait_newer while
    predating the effect.
    """
    kind = action.get("action")

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
            return {"stalled": None, "settle": None,
                    "noop": "drag_to had no tracked cursor position -- not performed"}
        x1, y1 = CURSOR["pos"]
        x2, y2 = (int(v) for v in action["coordinate"])
        # Re-assert the start before button-down (2026-07-21 review P1-8): the
        # tracked position can be stale if the pointer moved target-side since the
        # last action. Absolute pointing makes the correction free (same shape as
        # ApplianceClient.drag).
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
        # repeat_count clamped (second review #12): an unclamped model-supplied count
        # could fire hundreds of combos on one step.
        for _ in range(min(10, max(1, int(action.get("repeat_count", 1))))):
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
        if direction not in ("up", "down"):
            # v5 firmware wheel is single-axis vertical only -- left/right have no real
            # mapping. Report NOT-performed (second review #11) rather than letting the
            # diff read as an executed action that failed.
            print(f"[execute] scroll direction={direction!r} not supported by current "
                  f"firmware (vertical wheel only) -- no-op")
            return {"stalled": None, "settle": None,
                    "noop": f"scroll direction={direction!r} is not supported by the "
                            f"current firmware (vertical wheel only) -- not performed"}
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
        else:
            ENV.r4.scroll(-ticks)
    elif kind == "update_plan":
        PLAN["goals"] = action.get("goals", [])
        running = [g.get("title") for g in PLAN["goals"] if g.get("status") == "running"]
        print(f"[execute] plan updated ({len(PLAN['goals'])} goals, running: {running})")
        return  # no screen effect -> no settle wait
    elif kind == "finished":
        pass    # nothing to execute; run() handles this as loop-terminal
    else:
        print(f"[execute] unknown action kind {kind!r} -- no-op")
        return {"stalled": None, "settle": None,
                "noop": f"unknown action kind {kind!r} -- not performed"}

    # Freshness floor: the capture pipeline must advance PAST the fire before settling.
    # A stall here means the post-action frame may PREDATE the action (finding #6's
    # class) -- report it in the return value so run() can surface it to the model's
    # <tool_output> and the recorder instead of swallowing it as a print (2026-07-21
    # review P0-3). Not raised: the stall timeout shares the settle budget, so it can
    # fire on a merely slow render -- a warning, not a batch-killing error.
    seq0 = ENV.cam.seq   # AFTER the fire (second review #10), not at entry
    stalled = None
    try:
        ENV.cam.wait_newer(seq0, timeout_s=settle_s)
    except TimeoutError:
        stalled = f"capture stalled: no frame newer than seq={seq0} within {settle_s}s"
        print(f"[execute] WARNING: {stalled}")
    # Smart settle (2026-07-18): proceed the moment the UI stops changing, up to settle_s.
    # seq_fn: a wedged capture must not read as a settled UI (second review #1).
    settle = wait_until_stable(ENV.cam.read, settle_s, seq_fn=lambda: ENV.cam.seq)
    return {"stalled": stalled, "settle": settle, "noop": None}


def _region_name(row, col):
    """Human-readable location of a tile on the 9x16 grid (shared by the frame-diff
    detail and the pre-fire guard's refusal message)."""
    v = "top" if row < 3 else "bottom" if row >= 6 else "middle"
    h = "left" if col < 5 else "right" if col >= 11 else "center"
    return "center" if (v, h) == ("middle", "center") else f"{v}-{h}"


def _frame_diff_detail(png_a, png_b):
    """(score, region, changed_tiles) for the tile-max diff between two frames.

    The tile grid itself lives in kvm_agent.hardware.env.tile_means_png (single home,
    2026-07-21 review: the metric was implemented twice with identical hardcoded
    geometry that could drift apart silently). This wrapper adds WHICH tile peaked
    and HOW MANY of the 144 tiles crossed the threshold, so tool_output payloads can
    say WHAT changed, roughly WHERE, and whether it was localized or widespread (the
    2026-07-21/22 follow-up: a bare changed/unchanged binary confirmed
    real-but-irrelevant pixels -- taskbar focus visuals -- as action success at
    decision-critical steps, and the model typed blind on the false confirmation)."""
    tiles = tile_means_png(png_a, png_b)
    idx = int(tiles.argmax())
    row, col = divmod(idx, 16)
    changed_tiles = int((tiles > FRAME_CHANGE_THRESHOLD).sum())
    return float(tiles.max()), _region_name(row, col), changed_tiles


def _frame_diff_score(png_a, png_b):
    """Compat wrapper for tests/test_frame_diff.py: the score half of _frame_diff_detail."""
    return _frame_diff_detail(png_a, png_b)[0]


def _frame_changed(png_a, png_b, threshold=FRAME_CHANGE_THRESHOLD):
    return _frame_diff_detail(png_a, png_b)[0] > threshold


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
        no_progress_abort=True, session=None, verifier=None, verify_mode="off"):
    """Multi-step closed loop, native semantics: ground (against accumulated history) ->
    confirm (first N steps) -> execute the batch sequentially -> re-capture -> thread this
    step's observation + the assistant JSON turn + one <tool_output> per executed call
    into history -> repeat.

    session (roadmap Phase 1 seam, docs/ROADMAP.md Part 3 Slice C): a
    kvm_agent.models.base.ModelSession instance, defaulting to a fresh HoloSession.
    The loop never constructs Holo-specific state directly -- swap this to try a
    second model without touching anything below this line (the Phase-1 gate).

    History layout (native's, confirmed against the runtime binary's chat-mapper
    constants and hub.hcompany.ai/agent-loop): each successful step appends
      {"role": "user", "content": [<observation>+image+</observation>]}   (this step's own)
      {"role": "assistant", "content": "<the JSON string the model returned>"}
      {"role": "user", "content": "<tool_output tool=...>"}               (one per executed call)
    then trims to the last MAX_HISTORY_IMAGES screenshots (default 3 = native). The task
    instruction is sent ONLY on step 0's observation turn -- later turns carry it via
    history. Only the batch's FINAL screenshot is re-observed by the model (native's own
    batching rule). Steps whose JSON fails to PARSE (dropped steps) are NOT threaded
    into history -- referencing a malformed turn back to the model would confuse it
    more than a clean retry, and the response_format schema constraint makes these
    rare. Steps whose actions fail at EXECUTION time (ApplianceError) ARE threaded,
    error tool_output included -- the model must see the rejection to stop retrying
    the invalid action (2026-07-21 second review #2).

    confirm_first defaults to CONFIRM_FIRST; pass 0 to run unattended.

    record (default True) writes every step's pre-action frame, raw message, parsed step,
    token usage, and wall time to CFG.runs_dir/<tag>_<timestamp>/ via RunRecorder, plus a
    summary.json at the end. tag names the run directory (e.g. the task id in a battery).

    Returns {"finished": bool, "answer_text": str} (flaw #11: the battery runner needs
    the model's actual final answer text to tell an honest refusal apart from silently
    exhausting the step budget). answer_text is the `answer` tool's content; "" on every
    non-finished return (stuck limit / no-progress abort / max_steps).

    verify_mode / verifier (roadmap Phase 2 slice D-b, docs/PLAN_2026-07-22_phase2_
    subgoal_verification.md): postcondition verification of the model's OWN `finished`
    claim, by a kvm_agent.models.base.Verifier -- separate from and blind to `session`'s
    history (the whole point: it judges the pixels, not the actor's story about them).
      "off" (default): IDENTICAL to pre-D-b behavior -- verifier is never constructed or
        called, and the return dict has EXACTLY the two keys above (no new key appears
        uninvited; existing callers doing dict equality are unaffected).
      "shadow": the verifier judges the finished claim and the verdict is recorded (per-
        step in the recorder, and as this call's "verified_finish" key) but changes
        NOTHING about control flow or the return's finished/answer_text values -- this is
        how D-b measures live false-refusal/agreement without risking a working battery.
      "gate": a satisfied verdict accepts the finished claim. False OR None refuses it,
        threads a NOT accepted tool result with the oracle evidence back to the actor,
        and continues from a fresh observation. VERIFY_REFUSE_LIMIT refusals end the
        run failed with the distinct note "answer refused by verifier xN".
    When verify_mode != "off", every return path (not just the finished one) gains a
    "verified_finish" key -- None where no claim was ever made (aborts), the verdict
    dict where one was. Always present in that mode, so a consumer never has to guess
    whether `.get()` is needed.
    """
    confirm_first = CONFIRM_FIRST if confirm_first is None else confirm_first
    if verify_mode not in VERIFY_MODES:
        raise ValueError(f"verify_mode={verify_mode!r} must be one of {VERIFY_MODES}")
    if verify_mode != "off" and verifier is None:
        # Checked eagerly, not at the first `finished` claim: a run that never finishes
        # (stuck limit, max_steps) would otherwise let this misconfiguration hide for an
        # entire run, discovered only when someone notices verified_finish is always None.
        raise ValueError(f"verify_mode={verify_mode!r} requires a verifier")
    if MAX_HISTORY_IMAGES < 1:
        # 0 would evict EVERY screenshot (trim's n=0 contract) -- the model would run
        # blind and every step would read as a model failure (second review #12).
        raise ValueError(f"MAX_HISTORY_IMAGES={MAX_HISTORY_IMAGES} would blind the "
                         f"model (HOLO_HISTORY_IMAGES must be >= 1)")

    def _result(finished, answer_text_=""):
        """The return contract for every exit path below -- one place, so all six exit
        points (five aborts + the finished path) can't drift out of sync with each
        other on whether/how they report verification."""
        out = {"finished": finished, "answer_text": answer_text_}
        if verify_mode != "off":
            out["verified_finish"] = last_verify_verdict.to_dict() \
                if last_verify_verdict else None
        if verify_mode == "gate":
            out["verification_refusals"] = verify_refusals
        return out
    session = session or HoloSession(target=target, max_history_images=MAX_HISTORY_IMAGES,
                                     call_fn=call_holo_full)
    # Fresh per-run history, whether default-constructed or caller-injected (same
    # reasoning as the CURSOR/PLAN reset below -- a battery reboots the target between
    # tasks, so history from an injected session's previous run is wrong by definition).
    session.reset()
    LAST["history"] = session.history
    stuck = 0
    frozen = 0          # consecutive executed steps with no visible screen change
    click_repeat = 0    # consecutive steps whose last click landed in ~the same spot
    last_click = None
    guard_refusals = 0  # consecutive pre-fire guard refusals (see GUARD_REFUSE_LIMIT)
    verify_refusals = 0
    # Last verdict across the run, retained so a later max-steps/other abort still
    # exposes the reason its most recent finished claim was rejected.
    last_verify_verdict = None
    # Fresh per-run state (second review #12): the battery reboots the target between
    # tasks, so a tracked cursor/plan carried over from the previous run is wrong by
    # definition.
    CURSOR["pos"] = None
    PLAN["goals"] = []
    # Re-snapshot the serving layer per run, not per session: a battery runs for an
    # hour and the model can be evicted between tasks by any other consumer of the box
    # (reproduced 2026-07-23 -- one unrelated request evicted holo3.1). The reload is
    # ~17s and the client timeout is 180s, so it lands as latency, never an error. Only
    # a session that booted with serving_check pays for this (see SERVING).
    serving = serving_snapshot() if SERVING.get("checked") else None
    if serving and serving.get("resident") is False:
        print(f"[run] serving: {CFG.holo_model} is NOT resident -- this run's first "
              f"step pays a cold model load"
              + (f" (co-resident: {', '.join(str(m) for m in serving['co_resident'])})"
                 if serving.get("co_resident") else ""))
    recorder = RunRecorder(tag, instruction, target=target,
                            meta={"max_steps": max_steps,
                                  "screen_size": (ENV.screen_width, ENV.screen_height),
                                  "model_input_res": CFG.holo_model_input_res,
                                  "history_images": MAX_HISTORY_IMAGES,
                                  # The prompt the model ran under, kept WITH the run
                                  # (second review #7): previously only in the global
                                  # holo_requests.jsonl, correlated by timestamp.
                                  "system_prompt": SYSTEM_PROMPT,
                                  # ...and what the SERVER did to the image and the
                                  # context (2026-07-23): --image-min-tokens, ctx, KV
                                  # cache types, mmproj. model_input_res above is only
                                  # the client half of the model-input contract; the
                                  # other half lives in a config outside this repo.
                                  "serving": serving}) if record else None
    for step_i in range(max_steps):
        png, data_url = _capture_step_frames()
        LAST["png"] = png
        w, h = ENV.screen_width, ENV.screen_height
        step_instruction = instruction if step_i == 0 else ""
        t0 = time.time()
        try:
            decision = session.decide(data_url, w, h, step_instruction)
        except Exception as e:
            # A model-call failure (API error, 180s timeout) must NOT propagate: an
            # unguarded raise skips recorder.finish() (no summary.json) and, via the
            # battery's bare try/finally, kills every remaining task (2026-07-21 review
            # P0-2). Treat it exactly like a dropped step -- it counts against
            # STUCK_LIMIT, gets logged, and the recorder still finishes.
            decision = StepDecision(
                step={"actions": [], "note": None, "thought": None,
                      "error": f"model call failed: {e}"},
                message={}, usage={}, data_url=data_url, instruction=step_instruction)
        step, message, usage = decision.step, decision.message, decision.usage
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
                return _result(False)
            continue
        # NOTE: no stuck reset here (second review #2): resetting before execution
        # made the exec-error increment below dead code -- it could never reach
        # STUCK_LIMIT. The reset now happens only after a step completes cleanly.
        actions = step["actions"]

        if step_i < confirm_first:
            input(f"[run] step {step_i}: about to execute {actions} -- Enter to confirm...")

        # Execute the batch SEQUENTIALLY (native: one desktop, calls see each other's
        # effects; on error the remaining calls are skipped). One tool-result entry
        # per executed call carries our frame-diff what-changed signal; session.commit()
        # wraps each into native's <tool_output> shape.
        results = []
        answer_text = None
        exec_error = False
        guard_refused = False
        step_changed = False
        screen_touched = False   # has any action of THIS batch touched the screen yet?
        verify_verdict = None    # this step's verdict, if its batch ends in `finished`
        verify_refused = False
        for action in actions:
            kind = action.get("action")
            tool_name = session.tool_name(kind)
            before = _frame_png()
            # Pre-fire TOCTOU guard (see GUARD_KINDS note at top): `before` IS the
            # fresh pre-fire frame (grabbed just above, after the model's think time);
            # `png` is the decision frame -- the exact frame the model-input JPEG
            # derives from (single buffer read, second review #7). Refuse-to-fire on
            # change near the target; never fire-anyway, never inject a retry.
            if not screen_touched and kind in GUARD_KINDS and action.get("coordinate"):
                gx, gy = (int(v) for v in action["coordinate"])
                gscore, grow, gcol = tile_region_max_png(png, before, gx, gy, w, h)
                if gscore > FRAME_CHANGE_THRESHOLD:
                    gregion = _region_name(grow, gcol)
                    print(f"[run] step {step_i}: pre-fire guard refused {kind} at "
                          f"({gx},{gy}) -- region tile diff {gscore:.1f} since decision")
                    results.append((tool_name,
                        f"NOT executed: the screen changed near the target (region tile "
                        f"diff {gscore:.1f} at {gregion}) between your decision and "
                        f"firing -- the {kind} was not performed and the remaining "
                        f"calls in this step were not executed. Re-examine the next "
                        f"screenshot before retrying."))
                    step.setdefault("warnings", []).append(
                        f"guard_refusal: region tile diff {gscore:.1f} at {gregion}")
                    guard_refused = True
                    break
            try:
                exec_info = _execute(action)
            except ApplianceError as e:
                # A rejected/undeliverable action (e.g. a model-invented key name ->
                # bridge 502) halts the batch (native semantics) but must not kill the
                # run: count it like a dropped step (2026-07-21: 'winkey' crashed a
                # battery run at step 1 before this existed).
                print(f"[run] step {step_i}: exec error ({e}) -- batch halted")
                results.append((tool_name, f"Error: {e}. Remaining calls in this step were not executed."))
                exec_error = True
                break
            if kind == "update_plan":
                results.append((tool_name, "Plan updated."))
                continue
            after = _frame_png()
            score, region, changed_tiles = _frame_diff_detail(before, after)
            changed = score > FRAME_CHANGE_THRESHOLD
            step_changed = step_changed or changed
            if exec_info and exec_info.get("noop"):
                # The action was NOT performed (unsupported direction, no tracked
                # cursor, unknown kind). Say so explicitly (second review #11) --
                # otherwise "did not visibly change" reads as an executed action
                # that merely failed, and the model retries it.
                result = f"NOT executed: {exec_info['noop']}."
                step.setdefault("warnings", []).append(exec_info["noop"])
            else:
                if kind not in ("update_plan", "finished"):
                    screen_touched = True   # executed, screen-affecting: burns the guard
                # Magnitude + spread (2026-07-22): "changed" alone confirmed
                # real-but-irrelevant pixels (taskbar focus visuals) as success --
                # localized-vs-widespread + tile count give the model something to
                # judge relevance against.
                spread = "widespread" if changed_tiles >= 12 else "localized"
                result = (f"Executed. Screen changed: {spread} ({changed_tiles}/144 tiles, "
                          f"strongest {region}, max tile diff {score:.1f})."
                          if changed else
                          f"Executed. Screen did not visibly change (max tile diff {score:.1f}).")
            if exec_info:
                # Surface capture-health warnings to the model AND the recorder
                # (2026-07-21 review P0-3): the diff above may have compared a stale
                # frame, and the model is the one judging success from it.
                warns = []
                if exec_info.get("stalled"):
                    warns.append(exec_info["stalled"])
                if exec_info.get("settle") == "dead":
                    warns.append("capture dead: no frames delivered during the settle window")
                if warns:
                    result += " WARNING: " + "; ".join(warns) + \
                              " — the post-action frame may be stale."
                    step.setdefault("warnings", []).extend(warns)
            results.append((tool_name, result))
            if kind == "finished":
                answer_text = action.get("text", "")
                if verify_mode != "off":
                    # `after` (just above) IS the postcondition frame: the model's own
                    # `finished` action falls through _execute's ordinary settle path
                    # like any other action, so this is the exact same fresh frame the
                    # tool_output above is reporting on -- no extra capture. Encoded via
                    # the SAME client-side path the actor's own model input takes
                    # (png_to_model_input_jpeg @ CFG.holo_model_input_res), so a live
                    # verdict is comparable to tools/verify_replay.py's offline numbers
                    # that validated this oracle (slice D-a) rather than judging a
                    # differently-processed image.
                    verify_data_url = jpeg_bytes_to_data_url(
                        png_to_model_input_jpeg(after, CFG.holo_model_input_res))
                    try:
                        verify_verdict = verifier.check(verify_data_url, w, h,
                                                        instruction, claim=answer_text)
                    except Exception as e:
                        # Defense in depth: a CONFORMING verifier never raises for a
                        # model-side failure (kvm_agent.models.base.Verifier's own
                        # contract) -- HoloVerifier already converts those into
                        # Verdict(satisfied=None, ...). But an unexpected raise here
                        # would propagate past recorder.finish() and, via the battery's
                        # bare try/finally, kill every remaining task -- the same P0-2
                        # reasoning as the session.decide() guard above. Absorbed the
                        # same way rather than trusted to have honored its contract.
                        verify_verdict = Verdict(
                            satisfied=None, evidence=f"verifier call raised: {e}",
                            raw={}, usage={}, wall_time_s=0.0)
                    print(f"[run] step {step_i}: verify ({verify_mode}) satisfied="
                          f"{verify_verdict.satisfied} :: {verify_verdict.evidence[:160]}")
                    last_verify_verdict = verify_verdict
                    if verify_mode == "gate" and verify_verdict.satisfied is not True:
                        verify_refused = True
                        answer_text = None
                        status = ("unsatisfied" if verify_verdict.satisfied is False
                                  else "unanswered")
                        results[-1] = (
                            tool_name,
                            f"NOT accepted: the postcondition verifier was {status}: "
                            f"{verify_verdict.evidence}. The task is not finished. "
                            "Re-examine the next screenshot and continue.")
                break  # terminal: nothing after answer executes

        if recorder:
            recorder.log_step(step_i, png, message, step, usage, dt,
                              executed=not (exec_error or guard_refused),
                              verification=(verify_verdict.to_dict()
                                           if verify_verdict else None))

        if step.get("note"):
            print(f"[run] note: {step['note']!r}")

        # Thread this step into session history: own observation (without re-rendering
        # the instruction -- it appears on the live call only) + assistant JSON +
        # results. Exec-error steps are threaded TOO (second review #2): the error
        # tool_output is the only way the model learns its action was rejected and
        # which earlier batch calls DID execute -- discarding it made the model repeat
        # the invalid action and burn the budget. Only PARSE failures stay unthreaded
        # (a malformed turn would confuse the model more than a clean retry) -- the
        # `continue` on step.get("error") above skips this call entirely.
        session.commit(decision, results)

        if verify_refused:
            verify_refusals += 1
            print(f"[run] answer refused by verifier "
                  f"({verify_refusals}/{VERIFY_REFUSE_LIMIT})")
            if verify_refusals >= VERIFY_REFUSE_LIMIT:
                note = f"answer refused by verifier x{verify_refusals}"
                print(f"[run] {note} -- aborting")
                if recorder:
                    recorder.finish(False, note=note)
                return _result(False)
            # Refusal is its own bounded circuit breaker. It is not an execution error,
            # guard refusal, or frozen-screen step; the next iteration is the promised
            # fresh observation.
            continue

        if answer_text is not None:
            print(f"[run] finished: {answer_text!r}")
            if recorder:
                recorder.finish(True, note=answer_text)
            return _result(True, answer_text)

        if guard_refused:
            # Not a model failure (the model's click was correct against its decision
            # frame) -- so no STUCK_LIMIT increment and no no-progress accounting. The
            # next iteration's fresh capture IS the re-observe. But a permanently
            # animated target region (spinner, clock, video) would refuse forever:
            # abort loudly after GUARD_REFUSE_LIMIT consecutive refusals.
            guard_refusals += 1
            if guard_refusals >= GUARD_REFUSE_LIMIT:
                print(f"[run] target region unstable across {guard_refusals} decision "
                      f"cycles -- aborting")
                if recorder:
                    recorder.finish(False, note=f"target region unstable across "
                                                f"{guard_refusals} decision cycles")
                return _result(False)
            continue
        if exec_error:
            stuck += 1
            if stuck >= STUCK_LIMIT:
                print("[run] stuck limit hit (exec errors) -- aborting")
                if recorder:
                    recorder.finish(False, note="stuck limit hit (exec errors)")
                return _result(False)
            continue
        stuck = 0           # a cleanly completed step is the ONLY thing that resets these
        guard_refusals = 0

        # no-progress guards (flaw #9): abort instead of silently burning the budget.
        # (a) screen frozen -- consecutive executed steps with no visible change; (b) clustered
        # repeated clicks -- consecutive steps whose last click landed within ~25px (the
        # small_target_tray case, where clicks toggled a flyout so 'changed' was True but
        # nothing advanced).
        # Planning-only batches are exempt from (a) (2026-07-21 review P1-7): a step with
        # no screen-affecting action can never produce a frame change, so counting it as
        # "frozen" aborted legitimate planning runs 4 steps in.
        screen_actions = [a for a in actions if a.get("action") not in ("update_plan", "finished")]
        if screen_actions:
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
            return _result(False)
    print("[run] max_steps reached without finishing")
    if recorder:
        recorder.finish(False, note="max_steps reached")
    return _result(False)


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
