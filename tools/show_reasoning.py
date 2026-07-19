"""show_reasoning.py -- the FIRST place to look when a run.py/waa run goes wrong.

Every step's raw reasoning_content is already captured verbatim by RunRecorder
(kvm_agent/instrumentation/run_log.py's step_NN.json, message.reasoning_content) --
33/33 steps confirmed present on a real run, 2026-07-19. This just makes it readable
without opening N json files by hand, and flags the one pattern that has repeatedly
turned out to matter: the SAME action fired several steps in a row (the read that
looked like "the mouse can't click" tonight, before the real cause -- launching an
app doesn't reliably transfer Win32 keyboard focus -- was found by actually looking
at what happened, not by guessing from outside).

    python tools/show_reasoning.py                          # latest run in runs/
    python tools/show_reasoning.py waa__366de66e             # dir glob prefix
    python tools/show_reasoning.py runs/waa__.../             # exact dir
    python tools/show_reasoning.py --repeats-only waa__366de66e
"""
import argparse
import glob
import json
import os

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runs")


def _same_action(a, b, tol=25):
    """Loose equality for the repeat-detector: same kind, and (for clicks) within
    tol px, or (for type/key) identical payload. Mirrors run()'s own click_repeat
    idiom (agent_loop_holo.py, ~25px cluster)."""
    if a.get("action") != b.get("action"):
        return False
    kind = a.get("action")
    if kind == "left_click":
        ca, cb = a.get("coordinate"), b.get("coordinate")
        if not ca or not cb:
            return False
        return abs(ca[0] - cb[0]) <= tol and abs(ca[1] - cb[1]) <= tol
    if kind == "type":
        return a.get("text") == b.get("text")
    if kind == "key":
        return a.get("key") == b.get("key")
    return False


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

    prev_action = None
    repeat_run = 0
    n_missing_reasoning = 0
    for f in step_files:
        d = json.load(open(f))
        step = d.get("step")
        action = d.get("action", {})
        message = d.get("message", {})
        reasoning = message.get("reasoning_content")
        wall = d.get("wall_time_s")

        is_repeat = prev_action is not None and _same_action(action, prev_action)
        repeat_run = repeat_run + 1 if is_repeat else 0
        prev_action = action

        if repeats_only and not is_repeat:
            continue

        marker = f"  *** REPEAT #{repeat_run} of the same action ***" if is_repeat else ""
        kind = action.get("action")
        detail = (action.get("coordinate") or action.get("text") or action.get("key")
                   or action.get("direction") or "")
        print(f"--- step {step} ({wall:.1f}s) -> {kind} {detail}{marker}")
        if reasoning:
            print(f"    {reasoning.strip()}")
        else:
            n_missing_reasoning += 1
            print("    [no reasoning_content on this step's message]")
        print()

    if n_missing_reasoning:
        print(f"WARNING: {n_missing_reasoning}/{len(step_files)} steps had no reasoning_content "
              f"-- reasoning-mode may have been off for this run (see CFG.planner_thinking / "
              f"enable_thinking in the call), not a logging gap.")


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
