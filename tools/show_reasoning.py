"""show_reasoning.py -- the FIRST place to look when a run goes wrong.

Every step's raw reasoning_content is already captured verbatim by RunRecorder
(kvm_agent/instrumentation/run_log.py's step_NN.json, message.reasoning_content) --
33/33 steps confirmed present on a real run, 2026-07-19. This just makes it readable
without opening N json files by hand, and flags the one pattern that has repeatedly
turned out to matter: the SAME action fired several steps in a row (the read that
looked like "the mouse can't click" tonight, before the real cause -- launching an
app doesn't reliably transfer Win32 keyboard focus -- was found by actually looking
at what happened, not by guessing from outside).

Speaks the LIVE action vocabulary (2026-07-21 review P1-12): left_click,
double_click, move_to, drag_to, type, hotkey (keys list), hold_and_tap, scroll,
update_plan, finished -- and the batched step record shape (step_NN.json's "action"
field holds the whole parsed step dict, actions under "actions").

    python tools/show_reasoning.py                          # latest run in runs/
    python tools/show_reasoning.py battery_notepad_type     # dir glob prefix
    python tools/show_reasoning.py runs/battery_.../        # exact dir
    python tools/show_reasoning.py --repeats-only battery_notepad_type
"""
import argparse
import glob
import json
import os

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runs")


def _same_action(a, b, tol=25):
    """Loose equality for the repeat-detector: same kind, and (for pointer actions)
    within tol px, or (for type/hotkey/hold_and_tap) identical payload. Mirrors
    run()'s own click_repeat idiom (agent_loop_holo.py, ~25px cluster)."""
    if a.get("action") != b.get("action"):
        return False
    kind = a.get("action")
    if kind in ("left_click", "double_click", "move_to", "drag_to"):
        ca, cb = a.get("coordinate"), b.get("coordinate")
        if not ca or not cb:
            return False
        return abs(ca[0] - cb[0]) <= tol and abs(ca[1] - cb[1]) <= tol
    if kind == "scroll":
        ca, cb = a.get("coordinate"), b.get("coordinate")
        if ca and cb:
            return abs(ca[0] - cb[0]) <= tol and abs(ca[1] - cb[1]) <= tol
        return a.get("direction") == b.get("direction")
    if kind == "type":
        return a.get("text") == b.get("text")
    if kind == "hotkey":
        return a.get("keys") == b.get("keys")
    if kind == "hold_and_tap":
        return a.get("hold_keys") == b.get("hold_keys") and \
               a.get("tap_keys") == b.get("tap_keys")
    return False   # update_plan / finished / unknown: never a "repeat"


def _actions_of(d):
    """The action list from one step_NN.json record. Since the 2026-07-21
    native-verbatim rearchitecture the record's "action" field holds the whole
    parsed step dict ({"actions": [...], "note", ...}); pre-batch records held a
    single action dict directly."""
    step = d.get("action") or {}
    if "actions" in step:
        return step["actions"] or []
    return [step] if step.get("action") else []


def _same_step(actions, prev_actions):
    """A step repeats when it fires the identical action sequence as the prior step."""
    return (bool(actions) and len(actions) == len(prev_actions)
            and all(_same_action(a, b) for a, b in zip(actions, prev_actions)))


def find_run_dir(spec):
    if spec and os.path.isdir(spec):
        return spec
    pattern = os.path.join(ROOT, (spec or "") + "*")
    dirs = [d for d in glob.glob(pattern) if os.path.isdir(d)]
    if not dirs:
        raise SystemExit(f"no run directories match {pattern!r}")
    return max(dirs, key=os.path.getmtime)


def show(run_dir, repeats_only=False):
    meta_path = os.path.join(run_dir, "meta.json")
    if os.path.exists(meta_path):
        meta = json.load(open(meta_path))
        print(f"=== {os.path.basename(run_dir)} ===")
        print(f"goal: {meta.get('goal')!r}  target: {meta.get('target')}  started: {meta.get('started')}\n")

    step_files = sorted(glob.glob(os.path.join(run_dir, "step_*.json")))
    if not step_files:
        raise SystemExit(f"no step_*.json in {run_dir}")

    prev_actions = None
    repeat_run = 0
    n_missing_reasoning = 0
    for f in step_files:
        d = json.load(open(f))
        step = d.get("step")
        actions = _actions_of(d)
        message = d.get("message", {})
        reasoning = message.get("reasoning_content")
        wall = d.get("wall_time_s")

        is_repeat = prev_actions is not None and _same_step(actions, prev_actions)
        repeat_run = repeat_run + 1 if is_repeat else 0
        prev_actions = actions

        if repeats_only and not is_repeat:
            continue

        marker = f"  *** REPEAT #{repeat_run} of the same action ***" if is_repeat else ""
        kinds = "+".join(a.get("action", "?") for a in actions) or "(no actions)"
        detail = next((a.get("coordinate") or a.get("text") or a.get("keys")
                       or a.get("direction") for a in actions
                       if (a.get("coordinate") or a.get("text") or a.get("keys")
                           or a.get("direction"))), "")
        print(f"--- step {step} ({wall:.1f}s) -> {kinds} {detail}{marker}")
        if reasoning:
            print(f"    {reasoning.strip()}")
        else:
            n_missing_reasoning += 1
            print("    [no reasoning_content on this step's message]")
        print()

    if n_missing_reasoning:
        print(f"WARNING: {n_missing_reasoning}/{len(step_files)} steps had no reasoning_content "
              f"-- reasoning-mode may have been off for this run (see the enable_thinking "
              f"parameter of call_holo_full in kvm_agent/models/holo.py), not a logging gap.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", nargs="?", default=None,
                     help="run dir, or a glob prefix under runs/ (default: latest run overall)")
    ap.add_argument("--repeats-only", action="store_true",
                     help="only print steps that repeat the immediately-prior action -- the loop/stall signature")
    args = ap.parse_args()
    run_dir = find_run_dir(args.run)
    show(run_dir, repeats_only=args.repeats_only)


if __name__ == "__main__":
    main()
