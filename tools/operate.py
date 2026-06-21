"""
operate.py — minimal interactive operator for the EvoCUA rig.

Hand it a natural-language goal and it runs one S2 rollout against the physical
target until the model terminates (or a safety hook / Ctrl+C stops it). Unlike
run_probe.py (a benchmark harness), there is NO test coupling here: no expected
answer, no OCR verify-gate, no calculator-specific reset. The goal string IS the
prompt; the S2 system prompt + tool schema come from EvoCUAAgent.

Usage:
    python operate.py                      # REPL: boots the rig, then a `goal>` prompt
    python operate.py "open Calculator"    # run that goal first, then drop into the REPL
    python operate.py --once "do X"        # run one goal and exit (scriptable)
    python operate.py --confirm            # approve each action before the Pico fires it

In the REPL, Ctrl+C aborts the CURRENT goal and returns to the prompt; an empty line,
'quit', or Ctrl+D exits and releases the hardware.

Default is FREE-RUN (suitable for the hardware sandbox). Use --confirm on any machine
you care about. run_probe.py stays the benchmark / re-baseline harness.

[2-way street, later] This one-way loop (you give a goal, it acts) is the seed of a
bidirectional one. The attach points are marked TODO(2way): the agent's S2 `answer`
action is where the model would surface a question back to you, and the per-step gate
(--confirm) is where mid-run user steering would inject a corrective instruction
(reuse run_probe's corrective_instruction()).
"""
import os
import re
import sys
import json
import time
import hashlib
import argparse

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evocua")
sys.path.append(REPO)   # APPEND, not insert(0): keep repo root ahead of evocua/ so `import
                        # evocua_agent` loads the PATCHED root copy (5/5 formats + answer-channel),
                        # not the stale evocua/evocua_agent.py (1/5, no answer-channel). evocua/ is
                        # still on the path for mm_agents.* submodules. See DEMOS.md.
os.environ.setdefault("OPENAI_BASE_URL", "http://192.168.0.155:11434/v1")
os.environ.setdefault("OPENAI_API_KEY", "ollama")

from evocua_agent import EvoCUAAgent   # patched root copy (evocua/evocua_agent.py is shadowed)  # noqa: E402,F401
from cua_agent import make_agent       # backend selector (evocua | uitars)  # noqa: E402
from pico_env import PicoEnv           # noqa: E402

# Defaults = the proven S2 deployment config (matches run_probe's S2 entry).
MODEL        = "uitars-q4"
PROMPT_STYLE = "S2"
COORD_TYPE   = "relative"
RESIZE       = 32
MAX_PIXELS   = 16 * 16 * 4 * 12800   # full res by default. UNIFORM downscale to 1.28M was ~40% faster
                           # but degraded grounding on small targets (task failed, 2026-06-19) -- it
                           # shrinks the CURRENT frame the model is acting on. The right fix is
                           # asymmetric (current frame full-res, history frames downscaled). Lower this
                           # via --max-pixels only for experiments.
HISTORY_MAX_PIXELS = None   # asymmetric downscale: cap for HISTORY frames only (None = full res).
                           # The CURRENT frame the model grounds on is never shrunk, so this avoids
                           # the small-target misgrounding uniform --max-pixels caused. Try ~1_000_000
                           # via --history-max-pixels for the speed/grounding A/B.
HISTORY      = 4
TEMPERATURE  = 0.01
MAX_TOKENS   = 2048
SETTLE       = 1.0
MAX_STEPS    = 25
MAX_EMPTY_STREAK = 3    # consecutive zero-action steps -> abort (format/parse regression guard)


def extract_xy(cmd):
    m = re.search(r"pyautogui\.(?:click|moveTo|doubleClick|tripleClick|rightClick)\(\s*"
                  r"(?:x\s*=\s*)?(-?\d+)\s*,\s*(?:y\s*=\s*)?(-?\d+)", str(cmd))
    return (int(m.group(1)), int(m.group(2))) if m else None


def _ocr_text(png_bytes):
    """Best-effort OCR of a frame for verify-before-terminate. None if tesseract/PIL unavailable."""
    try:
        import io
        import pytesseract
        from PIL import Image
        return pytesseract.image_to_string(Image.open(io.BytesIO(png_bytes)))
    except Exception:
        return None


def _timing_summary(times, t0):
    """Print where the wall-clock went: model inference vs HID vs settle/overhead.
    `times` is a list of (it, dt_pred, dt_hid, dt_act) per step."""
    if not times:
        return
    total = time.time() - t0
    pred = sum(t[1] for t in times)
    hid  = sum(t[2] for t in times)
    act  = sum(t[3] for t in times)          # whole action phase (HID + settle)
    other = total - pred - act               # observe()/loop overhead
    settle = act - hid
    n = len(times)
    print("\n  ======== timing breakdown ========")
    print(f"  steps {n}   wall {total:.1f}s   {total/n:.1f}s/step")
    print(f"  model inference  : {pred:6.1f}s  {100*pred/total:4.0f}%   "
          f"(mean {pred/n:.1f}/step, first {times[0][1]:.1f}, last {times[-1][1]:.1f})")
    print(f"  HID execution    : {hid:6.1f}s  {100*hid/total:4.0f}%   (clicks + per-char typing)")
    print(f"  settle + overhead: {settle + other:6.1f}s  {100*(settle+other)/total:4.0f}%   "
          f"(settle ~{settle:.1f}, other ~{other:.1f})")
    slow = sorted(times, key=lambda x: x[1] + x[3], reverse=True)[:3]
    print("  slowest steps    : " +
          ", ".join(f"[{it}] {dp+da:.1f}s (pred {dp:.1f} / hid {dh:.1f})" for it, dp, dh, da in slow))
    print("  ==================================")


def run_goal(agent, env, goal, confirm=False, settle=SETTLE, max_steps=MAX_STEPS, expect=None):
    """Run ONE rollout for `goal`. Returns 'DONE' | 'FAIL' | 'ABORT' | 'DONE_FALSE' | None (max steps).
    expect: if set, verify-before-terminate — OCR the screen on DONE and only accept success if found."""
    agent.reset()
    obs = env.observe()          # current screen; NO physical reset click
    recent, empty_streak = [], 0
    times = []                   # per-step (it, dt_pred, dt_hid, dt_act) for the breakdown
    t0 = time.time()

    # ---- run logging: frames + manifest so every rollout is analyzable (like flail_frames/) ----
    run_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs",
                           f"{getattr(agent, 'model', 'run')}_{time.strftime('%Y%m%d_%H%M%S')}")
    try:
        os.makedirs(os.path.join(run_dir, "frames"), exist_ok=True)
        with open(os.path.join(run_dir, "goal.txt"), "w") as _g:
            _g.write(goal)
        print(f"  [log] {run_dir}")
    except Exception as e:
        print(f"  [log] could not create run dir ({e}); continuing without logging")
        run_dir = None
    manifest = []
    seen_sha = {}   # stuck-state guard: frame-sha -> times the model has acted on that screen
    clear_count = 0   # recovery-loop guard: times the model said it cleared/reset without converging

    for it in range(max_steps):
        t_pred = time.time()
        try:
            response, actions = agent.predict(goal, obs)
        except Exception as e:
            print(f"  [{it}] predict error: {e}")
            _timing_summary(times, t0); return None
        dt_pred = time.time() - t_pred

        # ---- log the frame the model just acted on + its response (Thought/Action) ----
        if run_dir:
            try:
                fb = obs.get("screenshot")
                sha = hashlib.sha256(fb).hexdigest()[:10] if fb else None
                if fb:
                    with open(os.path.join(run_dir, "frames", f"step{it:03d}.png"), "wb") as _f:
                        _f.write(fb)
                m = re.search(r"Thought:\s*(.+?)(?:\s*Action:|$)", response or "", re.S)
                manifest.append({
                    "step": it, "sha": sha,
                    "dup_of_prev": bool(manifest) and manifest[-1].get("sha") == sha,
                    "thought": (m.group(1).strip() if m else ""),
                    "actions": actions,
                    "xy": extract_xy(actions[-1]) if actions else None,
                    "pred_s": round(dt_pred, 1),
                    "response": response,
                })
                json.dump(manifest, open(os.path.join(run_dir, "manifest.json"), "w"), indent=2)
            except Exception as e:
                print(f"  [log] step {it} log failed: {e}")

        # stuck-state guard: if the model keeps acting on the SAME screen (clear/retry loops,
        # EvoCUA-style open/close loops), abort instead of burning the budget. Progress changes
        # the frame each step, so identical frames recurring = stuck.
        _fb = obs.get("screenshot") if isinstance(obs, dict) else None
        if _fb and actions:
            _sha = hashlib.sha256(_fb).hexdigest()[:10]
            seen_sha[_sha] = seen_sha.get(_sha, 0) + 1
            if seen_sha[_sha] >= 3:
                print(f"  stuck: same screen acted on {seen_sha[_sha]}x -> abort")
                _timing_summary(times, t0); return "ABORT"

        # recovery-loop guard: model keeps clearing/restarting but never converges -> abort
        if response and re.search(r"\b(clear|reset|start over|isn'?t correct|not correct|mistake)\b",
                                  response, re.I):
            clear_count += 1
            if clear_count >= 4:
                print(f"  recovery loop: cleared/reset {clear_count}x without success -> abort")
                _timing_summary(times, t0); return "ABORT"

        # dropped-action guard (same failure class the tool_call fix targets).
        # PATCH(answer-channel): a parsed answer is a real step, not a dropped action -- don't
        # count it toward the empty streak (it also always carries a token, so this rarely
        # matters, but it keeps the guard honest about communicative turns).
        if not actions and not getattr(agent, "last_answer", None):
            empty_streak += 1
            times.append((it, dt_pred, 0.0, 0.0))
            print(f"  [{it}] pred {dt_pred:.1f}s  (no action parsed; streak {empty_streak}) :: {(response or '')[:60]!r}")
            if empty_streak >= MAX_EMPTY_STREAK:
                print("  too many empty steps -> abort")
                _timing_summary(times, t0); return "ABORT"
            continue
        empty_streak = 0

        t_act = time.time(); dt_hid = 0.0
        for action in actions:
            if action in ("DONE", "FAIL"):
                if action == "DONE" and expect:   # verify-before-terminate
                    _txt = _ocr_text(obs.get("screenshot")) if isinstance(obs, dict) else None
                    if _txt is None:
                        print(f"        [verify] OCR unavailable; accepting DONE unverified")
                    elif expect.lower() in _txt.lower():
                        print(f"        [verify] PASS: '{expect}' is on screen")
                    else:
                        print(f"        [verify] FAIL: '{expect}' NOT on screen -> false terminate")
                        _timing_summary(times, t0); return "DONE_FALSE"
                rep = getattr(agent, "last_answer", None)
                if rep:
                    print(f"        model's reported answer: {rep!r}")
                times.append((it, dt_pred, dt_hid, time.time() - t_act))
                print(f"  [{it}] pred {dt_pred:.1f}s  TERMINATE -> {action}   (total {time.time()-t0:.1f}s)")
                _timing_summary(times, t0); return action
            if action == "ANSWER":  # PATCH(answer-channel): the 2-way street
                q = getattr(agent, "last_answer", None) or ""
                print(f"  [{it}] MODEL ASKS / REPORTS -> {q!r}")
                obs, _, _, _ = env.step(action, settle)   # no-op step; refreshes obs
                dt_hid += getattr(env, "last_exec_s", 0.0)
                try:
                    reply = input("        your reply (Enter to skip): ").strip()
                except EOFError:
                    reply = ""
                if reply:
                    goal = f"{goal}\n\n[user reply] {reply}"   # folded into the next predict
                continue
            if action == "WAIT":
                print(f"  [{it}] WAIT")
                obs, _, _, _ = env.step(action, settle)
                dt_hid += getattr(env, "last_exec_s", 0.0)
                continue
            xy = extract_xy(action)
            tag = f"   @ {xy}" if xy else ""
            print(f"  [{it}] {action}{tag}")
            # --confirm gate removed (runs free). The empty-streak + re-click-loop guards
            # below still bound a bad run, so unattended can't flail forever. Ctrl+C aborts.
            obs, _, _, _ = env.step(action, settle)
            dt_hid += getattr(env, "last_exec_s", 0.0)

        dt_act = time.time() - t_act
        times.append((it, dt_pred, dt_hid, dt_act))
        print(f"      ~time  pred {dt_pred:.1f}s  hid {dt_hid:.1f}s  act {dt_act:.1f}s")

        # free-run loop guard: 6 recent clicks collapsing to <=2 spots = stuck
        xy = extract_xy(actions[-1])
        if xy:
            recent = (recent + [xy])[-6:]
            if len(recent) >= 6:
                clusters = []
                for c in recent:
                    if not any(abs(c[0]-k[0]) < 15 and abs(c[1]-k[1]) < 15 for k in clusters):
                        clusters.append(c)
                if len(clusters) <= 2:
                    print("  re-click loop -> abort")
                    _timing_summary(times, t0); return "ABORT"

    print(f"  max steps ({max_steps}) reached without terminate")
    _timing_summary(times, t0); return None


def main():
    ap = argparse.ArgumentParser(description="Interactive operator for the EvoCUA rig.")
    ap.add_argument("goal", nargs="*", help="initial goal (optional); joined into one string")
    ap.add_argument("--backend", default="evocua", choices=["evocua", "uitars"],
                    help="which model adapter to drive the rig with")
    ap.add_argument("--model", default=None,
                    help="model/Ollama name; defaults per backend (evocua-8b-q5-clean | uitars-1.5-7b)")
    ap.add_argument("--history", type=int, default=HISTORY)
    ap.add_argument("--settle", type=float, default=SETTLE)
    ap.add_argument("--max-steps", type=int, default=MAX_STEPS)
    ap.add_argument("--max-pixels", type=int, default=MAX_PIXELS,
                    help="smart_resize cap; lower = downscale screenshots = fewer vision tokens/frame")
    ap.add_argument("--history-max-pixels", type=int, default=HISTORY_MAX_PIXELS,
                    help="asymmetric downscale: cap HISTORY frames only; current frame stays full-res")
    ap.add_argument("--confirm", action="store_true", help="(deprecated no-op; the per-action gate was removed, runs free)")
    ap.add_argument("--once", action="store_true", help="run the initial goal, then exit (no REPL)")
    ap.add_argument("--reset", action="store_true",
                    help="show the desktop (Win+D, via the rig's OWN Pico) before each goal for a clean start")
    ap.add_argument("--expect", default=None,
                    help="expected text on the final screen; verify-before-terminate (OCR, needs tesseract)")
    args = ap.parse_args()

    agent = make_agent(
        args.backend, model=args.model, history=args.history, temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS, max_pixels=args.max_pixels,
        history_max_pixels=args.history_max_pixels, max_steps=args.max_steps,
        screen_size=(1920, 1080),
    )
    print(f"[operate] backend={args.backend}  model={getattr(agent, 'model', '?')}")
    env = PicoEnv(cam_index=0, screen_size=(1920, 1080), show=False)

    def do(goal):
        goal = goal.strip()
        if not goal:
            return
        if args.reset:   # clean start via the rig's OWN Pico (never a standalone R4 connection)
            try:
                env.r4.combo("win+d"); time.sleep(1.2)
                print("  [reset] desktop shown (Win+D)")
            except Exception as e:
                print(f"  [reset] failed ({e}); continuing")
        print(f"\n=== goal: {goal!r} ===")
        try:
            res = run_goal(agent, env, goal, confirm=args.confirm, settle=args.settle,
                           max_steps=args.max_steps, expect=args.expect)
            print(f"=== result: {res} ===")
        except KeyboardInterrupt:
            try:
                env.r4.up()   # safety: release any held button before returning to prompt
            except Exception:
                pass
            print("\n  [interrupted] goal aborted; back to prompt")

    try:
        initial = " ".join(args.goal).strip()
        if initial:
            do(initial)
            if args.once:
                return
        while True:   # simple REPL (the seed of the future 2-way street)
            try:
                goal = input("\ngoal> ")
            except (EOFError, KeyboardInterrupt):
                break
            if goal.strip().lower() in ("", "quit", "exit", "q"):
                break
            do(goal)
    finally:
        env.close()
        print("hardware released. bye.")


if __name__ == "__main__":
    main()
